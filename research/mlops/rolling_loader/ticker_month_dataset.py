from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import secrets
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
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


DEFAULT_EVENT_OUTPUT_MODE = "raw_stream"
SUPPORTED_EVENT_OUTPUT_MODES = {"none", "raw_flat", "raw_stream", "raw_windows", "encoded_uint8"}
DEFAULT_DATA_GROUPS = ("events", "intraday_labels")
TEXT_CONTEXT_GROUPS = {"ticker_news_tokens", "market_news_tokens", "sec_filing_tokens"}
TEXT_INPUT_GROUP_TO_KEY = {
    "ticker_news_tokens": "ticker_news",
    "market_news_tokens": "market_news",
    "sec_filing_tokens": "sec_filings",
}
NUMERIC_EVENT_COLUMNS: tuple[str, ...] = tuple(column for column in (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS) if column != "ticker")
DEFAULT_SUPPRESSED_EVENT_COLUMNS = ("ticker_id", "ordinal", "timestamp_us")
LOADER_STATE_VERSION = 1
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
    event_stream_length: int = 1024
    event_stream_chunk_size: int = 128
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
    ticker_news_max_items: int = 8
    market_news_max_items: int = 16
    sec_filing_max_items: int = 4
    ticker_news_token_chunks: int = 2
    market_news_token_chunks: int = 2
    sec_filing_token_chunks: int = 8
    text_max_tokens: int = 1024
    event_columns: tuple[str, ...] = ()
    suppress_event_columns: tuple[str, ...] = DEFAULT_SUPPRESSED_EVENT_COLUMNS
    dataset_id: str = ""
    randomize_seed: bool = False
    sample_fraction: float = 1.0
    sample_hash_modulus: int = 0
    sample_hash_buckets: tuple[int, ...] = ()
    max_origins_per_epoch: int = 0
    materialize_chunk_size: int = 0
    drop_last_batch: bool = False
    preserve_batch_order: bool = True


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
    _event_matrices: dict[tuple[str, ...], np.ndarray] = field(default_factory=dict, init=False, repr=False)
    _origin_arrays: dict[str, np.ndarray] = field(default_factory=dict, init=False, repr=False)
    _label_arrays: dict[str, np.ndarray] = field(default_factory=dict, init=False, repr=False)
    _context_arrays: dict[tuple[str, str], np.ndarray] = field(default_factory=dict, init=False, repr=False)

    def event_array(self, column: str) -> np.ndarray:
        if self.events is None:
            raise RuntimeError("Part events were not loaded.")
        if column not in self._event_arrays:
            self._event_arrays[column] = self.events.get_column(column).to_numpy()
        return self._event_arrays[column]

    def event_matrix(self, columns: Sequence[str]) -> np.ndarray:
        if self.events is None:
            raise RuntimeError("Part events were not loaded.")
        key = tuple(str(column) for column in columns)
        if key not in self._event_matrices:
            self._event_matrices[key] = self.events.select(list(key)).to_numpy().astype(np.float32, copy=False)
        return self._event_matrices[key]

    def origin_array(self, column: str) -> np.ndarray:
        if self.origins is None:
            raise RuntimeError("Part origins were not loaded.")
        if column not in self._origin_arrays:
            self._origin_arrays[column] = self.origins.get_column(column).to_numpy()
        return self._origin_arrays[column]

    def label_array(self, column: str) -> np.ndarray:
        if self.labels is None:
            raise RuntimeError("Part labels were not loaded.")
        if column not in self._label_arrays:
            self._label_arrays[column] = self.labels.get_column(column).to_numpy()
        return self._label_arrays[column]

    def context_array(self, name: str, column: str) -> np.ndarray:
        if name not in self.context:
            raise RuntimeError(f"Context {name!r} was not loaded.")
        key = (str(name), str(column))
        if key not in self._context_arrays:
            self._context_arrays[key] = self.context[str(name)].get_column(str(column)).to_numpy()
        return self._context_arrays[key]


@dataclass(slots=True)
class TextContextIndex:
    timestamps_us: np.ndarray
    input_ids: np.ndarray
    attention_mask: np.ndarray
    chunk_mask: np.ndarray

    @property
    def item_count(self) -> int:
        return int(self.timestamps_us.shape[0])


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
    source_part_key: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=object))
    raw_event_windows: dict[str, np.ndarray] = field(default_factory=dict)
    raw_event_flat: dict[str, np.ndarray] = field(default_factory=dict)
    raw_event_stream: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0), dtype=np.float32))
    raw_event_stream_feature_names: tuple[str, ...] = ()
    raw_event_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    headers_uint8: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, HEADER_BYTES), dtype=np.uint8))
    events_uint8: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 128, EVENT_BYTES), dtype=np.uint8))
    intraday_labels: dict[str, np.ndarray] = field(default_factory=dict)
    future_intraday_bar_horizons: tuple[str, ...] = ()
    future_intraday_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32))
    future_intraday_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    input_availability: dict[str, np.ndarray] = field(default_factory=dict)
    text_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    external_context: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, float | int] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return int(self.origin_ordinal.shape[0])


@dataclass(slots=True)
class TickerMonthLoaderState:
    loader_state_version: int = LOADER_STATE_VERSION
    dataset_plan_id: str = ""
    cache_manifest_fingerprint: str = ""
    seed: int = 0
    epoch: int = 0
    package_position: int = 0
    origin_cursor: int = 0
    emitted_batches: int = 0
    emitted_samples: int = 0
    seen_origins_this_epoch: int = 0
    seen_origins_total: int = 0
    completed_epochs: int = 0
    total_available_origins: int = 0
    planned_origins: int = 0
    package_count: int = 0
    seen_by_month: dict[str, int] = field(default_factory=dict)
    seen_by_part: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loader_state_version": int(self.loader_state_version),
            "dataset_plan_id": str(self.dataset_plan_id),
            "cache_manifest_fingerprint": str(self.cache_manifest_fingerprint),
            "seed": int(self.seed),
            "epoch": int(self.epoch),
            "package_position": int(self.package_position),
            "origin_cursor": int(self.origin_cursor),
            "emitted_batches": int(self.emitted_batches),
            "emitted_samples": int(self.emitted_samples),
            "seen_origins_this_epoch": int(self.seen_origins_this_epoch),
            "seen_origins_total": int(self.seen_origins_total),
            "completed_epochs": int(self.completed_epochs),
            "total_available_origins": int(self.total_available_origins),
            "planned_origins": int(self.planned_origins),
            "package_count": int(self.package_count),
            "seen_by_month": dict(self.seen_by_month),
            "seen_by_part": dict(self.seen_by_part),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TickerMonthLoaderState":
        state = cls()
        for key in (
            "loader_state_version",
            "seed",
            "epoch",
            "package_position",
            "origin_cursor",
            "emitted_batches",
            "emitted_samples",
            "seen_origins_this_epoch",
            "seen_origins_total",
            "completed_epochs",
            "total_available_origins",
            "planned_origins",
            "package_count",
        ):
            if key in value:
                setattr(state, key, int(value[key] or 0))
        state.dataset_plan_id = str(value.get("dataset_plan_id") or "")
        state.cache_manifest_fingerprint = str(value.get("cache_manifest_fingerprint") or "")
        state.seen_by_month = {str(k): int(v or 0) for k, v in dict(value.get("seen_by_month") or {}).items()}
        state.seen_by_part = {str(k): int(v or 0) for k, v in dict(value.get("seen_by_part") or {}).items()}
        return state


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
        self._global_context_cache: dict[tuple[Path, str], Any] = {}

    def load(self, plan: TickerMonthPartPlan) -> LoadedTickerMonthPart:
        return self.load_payload(self.load_origins(plan))

    def load_origins(self, plan: TickerMonthPartPlan) -> LoadedTickerMonthPart:
        pl = _polars()
        loaded = LoadedTickerMonthPart(plan=plan)
        loaded.origins = pl.read_parquet(plan.package_dir / plan.files["origins"])
        return loaded

    def load_payload(self, loaded: LoadedTickerMonthPart) -> LoadedTickerMonthPart:
        pl = _polars()
        plan = loaded.plan
        need_events = bool({"events", "event_windows", "encoded_events"}.intersection(self.data_groups))
        need_labels = "intraday_labels" in self.data_groups or "labels" in self.data_groups
        if need_events:
            loaded.events = pl.read_parquet(plan.package_dir / plan.files["events"])
        if need_events and "event_window_index" in plan.files:
            loaded.windows = pl.read_parquet(plan.package_dir / plan.files["event_window_index"])
        if need_labels:
            loaded.labels = pl.read_parquet(plan.package_dir / plan.files["intraday_forward_labels"])
        if self.include_external_context or bool(TEXT_CONTEXT_GROUPS.intersection(self.data_groups)):
            for key, filename in _package_context_files(plan.package_dir).items():
                if key in self.data_groups:
                    loaded.context[key] = pl.read_parquet(plan.package_dir / filename)
            if "market_news_tokens" in self.data_groups:
                global_path = _month_global_dir(plan.package_dir) / "market_news_tokens.parquet"
                cache_key = (global_path, "market_news_tokens")
                if cache_key not in self._global_context_cache:
                    self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                loaded.context["market_news_tokens"] = self._global_context_cache[cache_key]
        return loaded


class TickerMonthBatchMaterializer:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.context_lags = tuple(index * int(self.config.context_stride_events) for index in range(int(self.config.context_chunks)))
        self._text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._global_text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._text_index_lock = threading.Lock()
        self.coverage_events = max(self.context_lags, default=0) + int(self.config.events_per_window)
        if self.config.event_output_mode == "raw_stream":
            self.coverage_events = int(self.config.event_stream_length)
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

    def clear_text_context_cache(self) -> None:
        with self._text_index_lock:
            self._text_index_cache.clear()

    def materialize(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> TickerMonthTrainingBatch:
        start = time.perf_counter()
        if not refs:
            return _empty_batch(self.config.event_output_mode)
        for part in parts:
            self.validate_part_config(part.plan)
        profile: dict[str, float | int] = {"samples": len(refs)}
        identity_start = time.perf_counter()
        tickers, ordinals, timestamps = _identity_arrays(parts, refs)
        source_part_key = _source_part_keys(parts, refs)
        profile["identity_seconds"] = time.perf_counter() - identity_start
        output_mode = str(self.config.event_output_mode)
        raw_windows: dict[str, np.ndarray] = {}
        raw_flat: dict[str, np.ndarray] = {}
        raw_stream = np.zeros((len(refs), 0, 0), dtype=np.float32)
        raw_stream_feature_names: tuple[str, ...] = ()
        raw_mask = np.zeros((len(refs), 0), dtype=np.bool_)
        headers = np.zeros((len(refs), 0, HEADER_BYTES), dtype=np.uint8)
        encoded_events = np.zeros((len(refs), 0, 128, EVENT_BYTES), dtype=np.uint8)
        event_start = time.perf_counter()
        if output_mode == "raw_windows":
            raw_windows = self._materialize_raw_windows(parts, refs)
        elif output_mode == "raw_flat":
            raw_flat, raw_mask = self._materialize_raw_flat(parts, refs)
        elif output_mode == "raw_stream":
            raw_stream, raw_stream_feature_names, raw_stream_profile = self._materialize_raw_stream(parts, refs)
            profile.update(raw_stream_profile)
        elif output_mode == "encoded_uint8":
            headers, encoded_events = self._materialize_encoded(parts, refs)
        profile["event_seconds"] = time.perf_counter() - event_start
        label_start = time.perf_counter()
        labels, future_bars, future_mask, horizons = self._materialize_intraday_labels(parts, refs)
        profile["label_seconds"] = time.perf_counter() - label_start
        availability = {
            "event_context_available": np.ones((len(refs),), dtype=np.bool_) if output_mode != "none" else np.zeros((len(refs),), dtype=np.bool_),
            "intraday_labels_available": future_mask.any(axis=1) if future_mask.size else np.zeros((len(refs),), dtype=np.bool_),
        }
        text_start = time.perf_counter()
        text_inputs, text_profile = self._materialize_text_inputs(parts, refs)
        for key, value in text_inputs.items():
            chunk_mask = value.get("chunk_mask")
            if chunk_mask is not None:
                availability[f"{key}_available"] = chunk_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(text_profile)
        profile["text_seconds"] = time.perf_counter() - text_start
        external_context = {}
        context_start = time.perf_counter()
        if self.config.include_external_context:
            external_context = _external_context_summary(parts)
        profile["context_seconds"] = time.perf_counter() - context_start
        profile["materialize_seconds"] = time.perf_counter() - start
        return TickerMonthTrainingBatch(
            ticker=tickers,
            origin_ordinal=ordinals,
            origin_timestamp_us=timestamps,
            event_output_mode=output_mode,
            source_part_key=source_part_key,
            raw_event_windows=raw_windows,
            raw_event_flat=raw_flat,
            raw_event_stream=raw_stream,
            raw_event_stream_feature_names=raw_stream_feature_names,
            raw_event_mask=raw_mask,
            headers_uint8=headers,
            events_uint8=encoded_events,
            intraday_labels=labels,
            future_intraday_bar_horizons=horizons,
            future_intraday_bars=future_bars,
            future_intraday_bar_mask=future_mask,
            input_availability=availability,
            text_inputs=text_inputs,
            external_context=external_context,
            profile=profile,
        )

    def _materialize_raw_windows(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> dict[str, np.ndarray]:
        starts = self._window_starts(parts, refs)
        out: dict[str, np.ndarray] = {}
        offsets = np.arange(int(self.config.events_per_window), dtype=np.int64)
        grouped_rows = _rows_by_part(refs)
        gather_indices = {
            part_index: starts[rows, :, None] + offsets[None, None, :]
            for part_index, rows in grouped_rows.items()
        }
        for column in _validated_event_columns_for_output(parts, self.config):
            dtype = _event_column_dtype(parts, column)
            values = np.empty((len(refs), len(self.context_lags), int(self.config.events_per_window)), dtype=dtype)
            for part_index, rows in grouped_rows.items():
                part = parts[int(part_index)]
                values[rows] = part.event_array(column)[gather_indices[part_index]]
            out[column] = values
        return out

    def _materialize_raw_stream(
        self,
        parts: Sequence[LoadedTickerMonthPart],
        refs: Sequence[TickerMonthSampleRef],
    ) -> tuple[np.ndarray, tuple[str, ...], dict[str, float | int]]:
        stage_start = time.perf_counter()
        stream_length = int(self.config.event_stream_length)
        columns = _validated_event_columns_for_output(parts, self.config)
        out = np.empty((len(refs), stream_length, len(columns)), dtype=np.float32)
        starts = _origin_event_offsets(parts, refs) - stream_length + 1
        if np.any(starts < 0):
            raise RuntimeError("Raw stream event coverage is out of bounds; rebuild cache with larger lookback or reduce event_stream_length.")
        offsets = np.arange(stream_length, dtype=np.int64)
        grouped_rows = _rows_by_part(refs)
        profile: dict[str, float | int] = {
            "raw_stream_validate_seconds": time.perf_counter() - stage_start,
            "raw_stream_matrix_seconds": 0.0,
            "raw_stream_gather_seconds": 0.0,
            "raw_stream_rows": int(len(refs)),
            "raw_stream_length": int(stream_length),
            "raw_stream_feature_count": int(len(columns)),
        }
        for part_index, rows in grouped_rows.items():
            part = parts[int(part_index)]
            event_ordinals = part.event_array("ordinal").astype(np.int64, copy=False)
            origin_rows = _origin_rows_for_refs(refs, rows)
            origin_ordinals = part.origin_array("origin_ordinal").astype(np.int64, copy=False)[origin_rows]
            event_offsets = part.origin_array("event_row_offset").astype(np.int64, copy=False)[origin_rows]
            part_starts = starts[rows]
            ends = part_starts + stream_length - 1
            if np.any(ends >= event_ordinals.shape[0]):
                raise RuntimeError("Raw stream exceeds loaded event rows.")
            if not bool(np.array_equal(event_ordinals[event_offsets], origin_ordinals)):
                raise RuntimeError(f"Raw stream origin row offsets are misaligned for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
            matrix_start = time.perf_counter()
            event_matrix = part.event_matrix(columns)
            profile["raw_stream_matrix_seconds"] = float(profile["raw_stream_matrix_seconds"]) + (time.perf_counter() - matrix_start)
            gather_start = time.perf_counter()
            gather_indices = part_starts[:, None] + offsets[None, :]
            window_ordinals = event_ordinals[gather_indices]
            if not bool(np.all(window_ordinals[:, -1] == origin_ordinals)):
                raise RuntimeError(f"Raw stream gathered window does not end at origin for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
            if stream_length > 1 and not bool(np.all(np.diff(window_ordinals, axis=1) == 1)):
                raise RuntimeError(f"Raw stream crosses an ordinal gap for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
            out[rows] = event_matrix[gather_indices]
            profile["raw_stream_gather_seconds"] = float(profile["raw_stream_gather_seconds"]) + (time.perf_counter() - gather_start)
        return out, columns, profile

    def _materialize_raw_flat(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[dict[str, np.ndarray], np.ndarray]:
        coverage = int(self.coverage_events)
        out: dict[str, np.ndarray] = {}
        mask = np.ones((len(refs), coverage), dtype=np.bool_)
        offsets = np.arange(coverage, dtype=np.int64)
        starts = _origin_event_offsets(parts, refs) - coverage + 1
        if np.any(starts < 0):
            raise RuntimeError("Raw flat event coverage is out of bounds; rebuild cache with larger lookback or reduce coverage.")
        grouped_rows = _rows_by_part(refs)
        gather_indices = {
            part_index: starts[rows, None] + offsets[None, :]
            for part_index, rows in grouped_rows.items()
        }
        for column in _validated_event_columns_for_output(parts, self.config):
            dtype = _event_column_dtype(parts, column)
            values = np.empty((len(refs), coverage), dtype=dtype)
            for part_index, rows in grouped_rows.items():
                part = parts[int(part_index)]
                values[rows] = part.event_array(column)[gather_indices[part_index]]
            out[column] = values
        return out, mask

    def _materialize_text_inputs(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
        requested = [group for group in ("ticker_news_tokens", "market_news_tokens", "sec_filing_tokens") if group in self.config.data_groups]
        if not requested:
            return {}, {}
        out: dict[str, dict[str, np.ndarray]] = {}
        profile: dict[str, float | int] = {
            "text_index_seconds": 0.0,
            "text_select_seconds": 0.0,
            "text_gather_seconds": 0.0,
        }
        origin_timestamps = _identity_arrays(parts, refs)[2]
        grouped_rows = _rows_by_part(refs)
        for group in requested:
            key = TEXT_INPUT_GROUP_TO_KEY[group]
            max_items, max_chunks = _text_group_limits(self.config, group)
            token_width = int(self.config.text_max_tokens)
            input_ids = np.zeros((len(refs), max_items, max_chunks, token_width), dtype=np.int32)
            attention_mask = np.zeros((len(refs), max_items, max_chunks, token_width), dtype=np.uint8)
            chunk_mask = np.zeros((len(refs), max_items, max_chunks), dtype=np.bool_)
            item_mask = np.zeros((len(refs), max_items), dtype=np.bool_)
            item_timestamp_us = np.zeros((len(refs), max_items), dtype=np.int64)
            if max_items <= 0:
                out[key] = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "chunk_mask": chunk_mask,
                    "item_mask": item_mask,
                    "item_timestamp_us": item_timestamp_us,
                }
                continue
            for part_index, rows in grouped_rows.items():
                part = parts[int(part_index)]
                frame = part.context.get(group)
                if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                    continue
                index_start = time.perf_counter()
                index = self._text_context_index(frame, group, max_chunks=max_chunks, token_width=token_width)
                profile["text_index_seconds"] = float(profile["text_index_seconds"]) + (time.perf_counter() - index_start)
                if index.item_count <= 0:
                    continue
                select_start = time.perf_counter()
                selected_indices, selected_mask = _select_text_item_indices(index, origin_timestamps[rows], max_items=max_items)
                profile["text_select_seconds"] = float(profile["text_select_seconds"]) + (time.perf_counter() - select_start)
                if not bool(selected_mask.any()):
                    continue
                gather_start = time.perf_counter()
                safe_indices = np.where(selected_mask, selected_indices, 0)
                gathered_ids = index.input_ids[safe_indices]
                gathered_attention = index.attention_mask[safe_indices]
                gathered_chunks = index.chunk_mask[safe_indices]
                gathered_timestamps = index.timestamps_us[safe_indices]
                invalid = ~selected_mask
                if bool(invalid.any()):
                    gathered_ids[invalid] = 0
                    gathered_attention[invalid] = 0
                    gathered_chunks[invalid] = False
                    gathered_timestamps[invalid] = 0
                input_ids[rows] = gathered_ids
                attention_mask[rows] = gathered_attention
                chunk_mask[rows] = gathered_chunks
                item_mask[rows] = selected_mask
                item_timestamp_us[rows] = gathered_timestamps
                profile["text_gather_seconds"] = float(profile["text_gather_seconds"]) + (time.perf_counter() - gather_start)
            out[key] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "chunk_mask": chunk_mask,
                "item_mask": item_mask,
                "item_timestamp_us": item_timestamp_us,
            }
        return out, profile

    def _text_context_index(self, frame: Any, group: str, *, max_chunks: int, token_width: int) -> TextContextIndex:
        key = (id(frame), str(group), int(max_chunks), int(token_width))
        cache = self._global_text_index_cache if str(group) == "market_news_tokens" else self._text_index_cache
        with self._text_index_lock:
            cached = cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_text_context_index(frame, group, max_chunks=max_chunks, token_width=token_width)
            cache[key] = index
            return index

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
        grouped_rows = _rows_by_part(refs)
        for part_index, rows in grouped_rows.items():
            part = parts[int(part_index)]
            ordinals = part.event_array("ordinal").astype(np.int64, copy=False)
            part_starts = starts[rows]
            ends = part_starts + int(self.config.events_per_window) - 1
            if np.any(ends >= ordinals.shape[0]):
                raise RuntimeError("Event window exceeds loaded event rows.")
            contiguous = (ordinals[ends] - ordinals[part_starts]) == (int(self.config.events_per_window) - 1)
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
        for part_index, rows in _rows_by_part(refs).items():
            part = parts[int(part_index)]
            if part.labels is None or part.labels.height == 0:
                if self.config.strict_audit:
                    first_row = int(rows[0]) if rows.shape[0] else 0
                    ref = refs[first_row]
                    origin = int(part.origin_array("origin_ordinal")[int(ref.origin_row)])
                    raise RuntimeError(f"Missing intraday labels for {part.plan.month}:{part.plan.ticker}|{origin}.")
                continue
            origin_rows = _origin_rows_for_refs(refs, rows)
            origins = part.origin_array("origin_ordinal").astype(np.int64, copy=False)[origin_rows]
            if _labels_are_pivoted(part.labels):
                label_ordinals = part.label_array("origin_ordinal").astype(np.int64, copy=False)
                label_indices = np.searchsorted(label_ordinals, origins, side="left")
                in_bounds = label_indices < label_ordinals.shape[0]
                found = np.zeros((origins.shape[0],), dtype=np.bool_)
                if np.any(in_bounds):
                    valid_positions = np.flatnonzero(in_bounds)
                    found[valid_positions] = label_ordinals[label_indices[valid_positions]] == origins[valid_positions]
                if self.config.strict_audit and not bool(found.all()):
                    missing_pos = int(np.flatnonzero(~found)[0])
                    raise RuntimeError(f"Missing intraday labels for {part.plan.month}:{part.plan.ticker}|{int(origins[missing_pos])}.")
                if not np.any(found):
                    continue
                output_rows = rows[found]
                source_indices = label_indices[found]
                for key, out in labels_out.items():
                    out[output_rows] = _gather_pivoted_label_column(part.label_array(key), source_indices, horizon_count, out.dtype)
                continue
            for output_row, origin in zip(rows, origins):
                label_values = _label_values_for_origin(part.labels, int(origin), horizon_count)
                if label_values is None:
                    if self.config.strict_audit:
                        raise RuntimeError(f"Missing intraday labels for {part.plan.month}:{part.plan.ticker}|{int(origin)}.")
                    continue
                for key in labels_out:
                    values = label_values[key]
                    labels_out[key][int(output_row), : values.shape[0]] = values.astype(labels_out[key].dtype, copy=False)
        available = labels_out["available"].astype(bool, copy=False)
        mask[:, : available.shape[1]] = available
        bars[:, :, FUTURE_BAR_FEATURE_KEYS.index("open")] = labels_out["price_primary_int"].astype(np.float32, copy=False)
        bars[:, :, FUTURE_BAR_FEATURE_KEYS.index("close")] = labels_out["price_primary_int"].astype(np.float32, copy=False)
        bars[:, :, FUTURE_BAR_FEATURE_KEYS.index("high")] = labels_out["price_primary_int"].astype(np.float32, copy=False)
        bars[:, :, FUTURE_BAR_FEATURE_KEYS.index("low")] = labels_out["price_secondary_int"].astype(np.float32, copy=False)
        bars[:, :, FUTURE_BAR_FEATURE_KEYS.index("volume")] = labels_out["size_primary_sum"].astype(np.float32, copy=False)
        return labels_out, bars, mask, horizons


class AsyncTickerMonthBatchLoader:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.index = TickerMonthCacheIndex(self.config)
        self.reader = TickerMonthPartReader(self.config.data_groups, include_external_context=self.config.include_external_context)
        self.materializer = TickerMonthBatchMaterializer(self.config)
        self.cache_manifest_fingerprint = _cache_plan_fingerprint(self.index.root_manifest, self.index.parts)
        self.dataset_plan_id = _dataset_plan_id(self.config, self.cache_manifest_fingerprint)
        seed = secrets.randbits(63) if self.config.randomize_seed else int(self.config.seed)
        self.state = TickerMonthLoaderState(
            dataset_plan_id=self.dataset_plan_id,
            cache_manifest_fingerprint=self.cache_manifest_fingerprint,
            seed=int(seed),
            epoch=0,
            total_available_origins=sum(int(plan.origin_count) for plan in self.index.parts),
            package_count=len(self.index.parts),
        )

    def iter_batches(self) -> Iterator[TickerMonthTrainingBatch]:
        if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
            return
        start_us = _parse_timestamp_us(self.config.start_utc) if self.config.start_utc else None
        end_us = _parse_timestamp_us(self.config.end_utc) if self.config.end_utc else None
        group_size = max(1, int(self.config.loaded_parts_per_group))
        plans = self._epoch_plans(int(self.state.epoch))
        ready = _ReadyBatchBuffer(batch_size=int(self.config.batch_size), drop_last=bool(self.config.drop_last_batch))
        with ThreadPoolExecutor(max_workers=max(1, int(self.config.read_workers)), thread_name_prefix="tmc-load") as read_pool:
            for group_start in range(int(self.state.package_position), len(plans), group_size):
                self.state.package_position = int(group_start)
                group_plans = plans[group_start : group_start + group_size]
                group_profile: dict[str, float] = {}
                stage_start = time.perf_counter()
                loaded_origins = list(read_pool.map(self.reader.load_origins, group_plans))
                group_profile["origin_load_seconds"] = time.perf_counter() - stage_start
                stage_start = time.perf_counter()
                refs = _sample_refs_for_loaded_parts(
                    loaded_origins,
                    config=self.config,
                    seed=int(self.state.seed),
                    dataset_plan_id=self.dataset_plan_id,
                    start_us=start_us,
                    end_us=end_us,
                )
                group_profile["sample_refs_seconds"] = time.perf_counter() - stage_start
                if self.config.shuffle_within_loaded_group and self.config.event_output_mode != "raw_stream":
                    random.Random(_stable_int_seed("origins", self.state.seed, self.state.epoch, group_start, self.dataset_plan_id)).shuffle(refs)
                group_origin_base = max(0, int(self.state.origin_cursor))
                if group_origin_base == 0:
                    self.state.planned_origins += len(refs)
                if group_origin_base > 0:
                    refs = refs[group_origin_base:]
                if not refs:
                    self.state.origin_cursor = 0
                    self.state.package_position = int(group_start) + len(group_plans)
                    continue
                active_part_indices = sorted({int(ref.part_index) for ref in refs})
                part_index_map = {old_index: new_index for new_index, old_index in enumerate(active_part_indices)}
                stage_start = time.perf_counter()
                loaded = list(read_pool.map(self.reader.load_payload, (loaded_origins[index] for index in active_part_indices)))
                group_profile["payload_load_seconds"] = time.perf_counter() - stage_start
                refs = [
                    TickerMonthSampleRef(part_index=int(part_index_map[int(ref.part_index)]), origin_row=int(ref.origin_row))
                    for ref in refs
                ]
                group_keys = {_part_key(part.plan) for part in loaded}
                emitted_from_group = 0
                group_profile_attached = False
                materialize_size = int(self.config.materialize_chunk_size) or int(self.config.batch_size)
                try:
                    with ThreadPoolExecutor(max_workers=max(1, int(self.config.materialize_workers)), thread_name_prefix="tmc-materialize") as mat_pool:
                        materialized = _materialize_bounded(
                            mat_pool,
                            self.materializer,
                            loaded,
                            _batched_refs(refs, materialize_size),
                            preserve_order=bool(self.config.preserve_batch_order),
                        )
                        for chunk in materialized:
                            chunk_ready_start = time.perf_counter()
                            if chunk.sample_count == 0:
                                continue
                            for batch in ready.add(chunk):
                                batch.profile["ready_concat_seconds"] = float(batch.profile.get("ready_concat_seconds", 0.0)) + (time.perf_counter() - chunk_ready_start)
                                if not group_profile_attached:
                                    for key, value in group_profile.items():
                                        batch.profile[key] = float(batch.profile.get(key, 0.0)) + float(value)
                                    group_profile_attached = True
                                batch = self._apply_epoch_sample_cap(batch)
                                if batch.sample_count == 0:
                                    return
                                emitted_from_group += _count_batch_samples_for_part_keys(batch, group_keys)
                                self.state.origin_cursor = int(group_origin_base) + int(emitted_from_group)
                                self._record_emitted_batch(batch)
                                yield batch
                                if int(self.config.max_batches) > 0 and int(self.state.emitted_batches) >= int(self.config.max_batches):
                                    return
                                if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                                    return
                finally:
                    self.materializer.clear_text_context_cache()
                self.state.origin_cursor = 0
                self.state.package_position = int(group_start) + len(group_plans)
                if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                    return
            for batch in ready.flush():
                if batch.sample_count == 0:
                    continue
                batch = self._apply_epoch_sample_cap(batch)
                if batch.sample_count == 0:
                    return
                self._record_emitted_batch(batch)
                yield batch
                if int(self.config.max_batches) > 0 and int(self.state.emitted_batches) >= int(self.config.max_batches):
                    return
                if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                    return
            self.state.completed_epochs += 1
            self.state.epoch += 1
            self.state.package_position = 0
            self.state.origin_cursor = 0
            self.state.seen_origins_this_epoch = 0

    def state_dict(self) -> dict[str, Any]:
        return self.state.to_dict()

    def load_state_dict(self, value: Mapping[str, Any]) -> None:
        state = TickerMonthLoaderState.from_dict(value)
        if state.loader_state_version != LOADER_STATE_VERSION:
            raise ValueError(f"Unsupported loader state version: {state.loader_state_version}")
        if state.dataset_plan_id and state.dataset_plan_id != self.dataset_plan_id:
            raise ValueError(f"Loader state dataset_plan_id={state.dataset_plan_id!r} does not match current plan {self.dataset_plan_id!r}.")
        if state.cache_manifest_fingerprint and state.cache_manifest_fingerprint != self.cache_manifest_fingerprint:
            raise ValueError("Loader state cache manifest fingerprint does not match current cache manifest.")
        state.dataset_plan_id = self.dataset_plan_id
        state.cache_manifest_fingerprint = self.cache_manifest_fingerprint
        state.total_available_origins = sum(int(plan.origin_count) for plan in self.index.parts)
        state.package_count = len(self.index.parts)
        self.state = state

    def summary(self) -> dict[str, Any]:
        out = self.state.to_dict()
        out["epoch_fraction"] = float(self.state.package_position) / max(float(len(self.index.parts)), 1.0)
        out["config"] = {
            "batch_size": int(self.config.batch_size),
            "event_output_mode": str(self.config.event_output_mode),
            "event_stream_length": int(self.config.event_stream_length),
            "materialize_chunk_size": int(self.config.materialize_chunk_size) or int(self.config.batch_size),
            "loaded_parts_per_group": int(self.config.loaded_parts_per_group),
            "sample_fraction": float(self.config.sample_fraction),
            "sample_hash_modulus": int(self.config.sample_hash_modulus),
            "sample_hash_buckets": list(self.config.sample_hash_buckets),
            "max_origins_per_epoch": int(self.config.max_origins_per_epoch),
            "randomize_seed": bool(self.config.randomize_seed),
        }
        return out

    def _epoch_plans(self, epoch: int) -> list[TickerMonthPartPlan]:
        plans = list(self.index.parts)
        if self.config.shuffle_parts:
            random.Random(_stable_int_seed("packages", self.state.seed, epoch, self.dataset_plan_id)).shuffle(plans)
        return plans

    def _record_emitted_batch(self, batch: TickerMonthTrainingBatch) -> None:
        samples = int(batch.sample_count)
        self.state.emitted_batches += 1
        self.state.emitted_samples += samples
        self.state.seen_origins_this_epoch += samples
        self.state.seen_origins_total += samples
        if batch.source_part_key.shape[0] != samples:
            return
        keys, counts = np.unique(batch.source_part_key.astype(str, copy=False), return_counts=True)
        for key, count_value in zip(keys, counts):
            key = str(key)
            count = int(count_value)
            month = key.split("|", 1)[0]
            self.state.seen_by_month[month] = int(self.state.seen_by_month.get(month, 0)) + count
            self.state.seen_by_part[key] = int(self.state.seen_by_part.get(key, 0)) + count

    def _apply_epoch_sample_cap(self, batch: TickerMonthTrainingBatch) -> TickerMonthTrainingBatch:
        cap = int(self.config.max_origins_per_epoch)
        if cap <= 0:
            return batch
        remaining = cap - int(self.state.seen_origins_this_epoch)
        if remaining <= 0:
            return _slice_training_batch(batch, 0, 0)
        if batch.sample_count <= remaining:
            return batch
        return _slice_training_batch(batch, 0, remaining)


def normalize_loader_config(config: TickerMonthLoaderConfig) -> TickerMonthLoaderConfig:
    mode = str(config.event_output_mode)
    if mode not in SUPPORTED_EVENT_OUTPUT_MODES:
        raise ValueError(f"Unsupported event_output_mode={mode!r}; expected one of {sorted(SUPPORTED_EVENT_OUTPUT_MODES)}")
    groups = tuple(dict.fromkeys(str(group) for group in config.data_groups))
    if mode in {"raw_flat", "raw_stream", "raw_windows"} and "events" not in groups:
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
        event_stream_length=max(1, int(config.event_stream_length)),
        event_stream_chunk_size=max(1, int(config.event_stream_chunk_size)),
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
        ticker_news_max_items=max(0, int(config.ticker_news_max_items)),
        market_news_max_items=max(0, int(config.market_news_max_items)),
        sec_filing_max_items=max(0, int(config.sec_filing_max_items)),
        ticker_news_token_chunks=max(1, int(config.ticker_news_token_chunks)),
        market_news_token_chunks=max(1, int(config.market_news_token_chunks)),
        sec_filing_token_chunks=max(1, int(config.sec_filing_token_chunks)),
        text_max_tokens=max(1, int(config.text_max_tokens)),
        event_columns=tuple(str(column) for column in config.event_columns),
        suppress_event_columns=tuple(str(column) for column in config.suppress_event_columns),
        dataset_id=str(config.dataset_id),
        randomize_seed=bool(config.randomize_seed),
        sample_fraction=min(max(float(config.sample_fraction), 0.0), 1.0),
        sample_hash_modulus=max(0, int(config.sample_hash_modulus)),
        sample_hash_buckets=tuple(int(bucket) for bucket in config.sample_hash_buckets),
        max_origins_per_epoch=max(0, int(config.max_origins_per_epoch)),
        materialize_chunk_size=max(0, int(config.materialize_chunk_size)),
        drop_last_batch=bool(config.drop_last_batch),
        preserve_batch_order=bool(config.preserve_batch_order),
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


def _month_global_dir(package_dir: Path) -> Path:
    for parent in Path(package_dir).parents:
        if parent.name.startswith("month="):
            return parent / "global"
    return Path(package_dir).parent / "global"


def _package_context_files(package_dir: Path) -> dict[str, str]:
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path)
    files = manifest.get("files") or {}
    return {str(key): str(value) for key, value in files.items() if key in {"ticker_news_tokens", "sec_filing_tokens", "xbrl", "daily_bars"}}


def _text_group_limits(config: TickerMonthLoaderConfig, group: str) -> tuple[int, int]:
    if group == "ticker_news_tokens":
        return max(0, int(config.ticker_news_max_items)), max(1, int(config.ticker_news_token_chunks))
    if group == "market_news_tokens":
        return max(0, int(config.market_news_max_items)), max(1, int(config.market_news_token_chunks))
    if group == "sec_filing_tokens":
        return max(0, int(config.sec_filing_max_items)), max(1, int(config.sec_filing_token_chunks))
    return 0, 1


def _prepare_text_context_rows(frame: Any, group: str) -> list[dict[str, Any]]:
    if int(getattr(frame, "height", 0) or 0) <= 0:
        return []
    required = {"timestamp_us", "token_chunk_index", "input_ids", "attention_mask"}
    if not required.issubset(set(getattr(frame, "columns", ()))):
        return []
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in frame.iter_rows(named=True):
        timestamp_us = int(row.get("timestamp_us") or 0)
        item_key = _text_item_key(row, group)
        item = grouped.setdefault(item_key, {"timestamp_us": timestamp_us, "item_key": item_key, "rows": []})
        item["timestamp_us"] = max(int(item["timestamp_us"]), timestamp_us)
        item["rows"].append(row)
    items = list(grouped.values())
    for item in items:
        item["rows"].sort(key=lambda row: int(row.get("token_chunk_index", 0) or 0))
    items.sort(key=lambda item: (-int(item["timestamp_us"]), item["item_key"]))
    return items


def _text_item_key(row: Mapping[str, Any], group: str) -> tuple[Any, ...]:
    if group == "sec_filing_tokens":
        return (
            str(row.get("accession_number") or ""),
            str(row.get("document_id") or ""),
            int(row.get("text_rank") or 0),
            str(row.get("source_id") or ""),
        )
    return (
        str(row.get("source_id") or ""),
        str(row.get("provider_article_id") or ""),
        str(row.get("text_hash") or ""),
    )


def _select_text_items(items: Sequence[Mapping[str, Any]], origin_timestamp_us: int, *, max_items: int) -> list[Mapping[str, Any]]:
    if max_items <= 0:
        return []
    selected = [item for item in items if int(item.get("timestamp_us") or 0) <= int(origin_timestamp_us)]
    return selected[: int(max_items)]


def _prepare_text_context_index(frame: Any, group: str, *, max_chunks: int, token_width: int) -> TextContextIndex:
    max_chunks = max(1, int(max_chunks))
    token_width = max(1, int(token_width))
    if int(getattr(frame, "height", 0) or 0) <= 0:
        return _empty_text_context_index(max_chunks=max_chunks, token_width=token_width)
    required = {"timestamp_us", "token_chunk_index", "input_ids", "attention_mask"}
    if not required.issubset(set(getattr(frame, "columns", ()))):
        return _empty_text_context_index(max_chunks=max_chunks, token_width=token_width)
    height = int(frame.height)
    timestamps = frame.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
    chunk_indices = frame.get_column("token_chunk_index").to_numpy()
    input_values = frame.get_column("input_ids").to_numpy()
    mask_values = frame.get_column("attention_mask").to_numpy()
    columns = set(frame.columns)
    source_id = _optional_frame_column(frame, "source_id", "")
    if group == "sec_filing_tokens":
        accession_number = _optional_frame_column(frame, "accession_number", "")
        document_id = _optional_frame_column(frame, "document_id", "")
        text_rank = _optional_frame_column(frame, "text_rank", 0)
        grouped: dict[tuple[Any, ...], list[int]] = {}
        item_timestamp: dict[tuple[Any, ...], int] = {}
        for row_index in range(height):
            key = (
                str(accession_number[row_index] if "accession_number" in columns else ""),
                str(document_id[row_index] if "document_id" in columns else ""),
                _safe_int(text_rank[row_index] if "text_rank" in columns else 0),
                str(source_id[row_index] if "source_id" in columns else ""),
            )
            grouped.setdefault(key, []).append(row_index)
            item_timestamp[key] = max(int(item_timestamp.get(key, 0)), int(timestamps[row_index]))
    else:
        provider_article_id = _optional_frame_column(frame, "provider_article_id", "")
        text_hash = _optional_frame_column(frame, "text_hash", "")
        grouped = {}
        item_timestamp = {}
        for row_index in range(height):
            key = (
                str(source_id[row_index] if "source_id" in columns else ""),
                str(provider_article_id[row_index] if "provider_article_id" in columns else ""),
                str(text_hash[row_index] if "text_hash" in columns else ""),
            )
            grouped.setdefault(key, []).append(row_index)
            item_timestamp[key] = max(int(item_timestamp.get(key, 0)), int(timestamps[row_index]))
    items = sorted(((int(item_timestamp[key]), key, rows) for key, rows in grouped.items()), key=lambda item: (item[0], item[1]))
    if not items:
        return _empty_text_context_index(max_chunks=max_chunks, token_width=token_width)
    out_timestamps = np.empty((len(items),), dtype=np.int64)
    out_ids = np.zeros((len(items), max_chunks, token_width), dtype=np.int32)
    out_masks = np.zeros((len(items), max_chunks, token_width), dtype=np.uint8)
    out_chunk_mask = np.zeros((len(items), max_chunks), dtype=np.bool_)
    for item_index, (timestamp_us, _key, rows) in enumerate(items):
        out_timestamps[item_index] = int(timestamp_us)
        rows = sorted(rows, key=lambda row: _safe_int(chunk_indices[row]))
        for row_index in rows:
            chunk_index = _safe_int(chunk_indices[row_index])
            if chunk_index < 0 or chunk_index >= max_chunks:
                continue
            ids = _cell_array(input_values[row_index]).astype(np.int32, copy=False)
            mask = _cell_array(mask_values[row_index]).astype(np.uint8, copy=False)
            width = min(token_width, int(ids.shape[0]), int(mask.shape[0]))
            if width <= 0:
                continue
            out_ids[item_index, chunk_index, :width] = ids[:width]
            out_masks[item_index, chunk_index, :width] = mask[:width]
            out_chunk_mask[item_index, chunk_index] = bool(mask[:width].any())
    return TextContextIndex(timestamps_us=out_timestamps, input_ids=out_ids, attention_mask=out_masks, chunk_mask=out_chunk_mask)


def _select_text_item_indices(index: TextContextIndex, origin_timestamps_us: np.ndarray, *, max_items: int) -> tuple[np.ndarray, np.ndarray]:
    origins = np.asarray(origin_timestamps_us, dtype=np.int64)
    max_items = max(0, int(max_items))
    if max_items <= 0 or index.item_count <= 0 or origins.shape[0] <= 0:
        return np.full((int(origins.shape[0]), max_items), -1, dtype=np.int64), np.zeros((int(origins.shape[0]), max_items), dtype=np.bool_)
    rightmost = np.searchsorted(index.timestamps_us, origins, side="right") - 1
    offsets = np.arange(max_items, dtype=np.int64)
    indices = rightmost[:, None] - offsets[None, :]
    valid = indices >= 0
    return np.where(valid, indices, -1).astype(np.int64, copy=False), valid


def _empty_text_context_index(*, max_chunks: int, token_width: int) -> TextContextIndex:
    return TextContextIndex(
        timestamps_us=np.zeros((0,), dtype=np.int64),
        input_ids=np.zeros((0, max(1, int(max_chunks)), max(1, int(token_width))), dtype=np.int32),
        attention_mask=np.zeros((0, max(1, int(max_chunks)), max(1, int(token_width))), dtype=np.uint8),
        chunk_mask=np.zeros((0, max(1, int(max_chunks))), dtype=np.bool_),
    )


def _optional_frame_column(frame: Any, name: str, default: Any) -> np.ndarray:
    if name in getattr(frame, "columns", ()):
        return frame.get_column(name).to_numpy()
    return np.full((int(getattr(frame, "height", 0) or 0),), default, dtype=object)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _sample_refs_for_loaded_parts(
    loaded: Sequence[LoadedTickerMonthPart],
    *,
    config: TickerMonthLoaderConfig,
    seed: int,
    dataset_plan_id: str,
    start_us: int | None,
    end_us: int | None,
) -> list[TickerMonthSampleRef]:
    refs: list[TickerMonthSampleRef] = []
    for part_index, part in enumerate(loaded):
        if part.origins is None or part.origins.height == 0:
            continue
        ts = part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)
        mask = np.ones((ts.shape[0],), dtype=np.bool_)
        if start_us is not None:
            mask &= ts >= int(start_us)
        if end_us is not None:
            mask &= ts < int(end_us)
        if config.event_output_mode == "raw_stream":
            offsets = part.origin_array("event_row_offset").astype(np.int64, copy=False)
            mask &= offsets >= (int(config.event_stream_length) - 1)
        candidate_rows = np.flatnonzero(mask)
        if _uses_dataset_hash_filter(config):
            if _uses_fast_fraction_filter(config):
                candidate_rows = _fast_fraction_candidate_rows(part.plan, candidate_rows, config=config, seed=seed, dataset_plan_id=dataset_plan_id)
            else:
                ordinals = part.origin_array("origin_ordinal").astype(np.int64, copy=False)
                selected: list[int] = []
                for row in candidate_rows:
                    if _sample_selected(part.plan, int(ordinals[int(row)]), int(ts[int(row)]), config=config, seed=seed, dataset_plan_id=dataset_plan_id):
                        selected.append(int(row))
                candidate_rows = np.asarray(selected, dtype=np.int64)
        for row in candidate_rows:
            refs.append(TickerMonthSampleRef(part_index=int(part_index), origin_row=int(row)))
    return refs


def _batched_refs(refs: Sequence[TickerMonthSampleRef], batch_size: int) -> Iterator[list[TickerMonthSampleRef]]:
    size = max(1, int(batch_size))
    for start in range(0, len(refs), size):
        yield list(refs[start : start + size])


class _ReadyBatchBuffer:
    def __init__(self, *, batch_size: int, drop_last: bool) -> None:
        self.batch_size = max(1, int(batch_size))
        self.drop_last = bool(drop_last)
        self._chunks: list[TickerMonthTrainingBatch] = []
        self._samples = 0

    def add(self, batch: TickerMonthTrainingBatch) -> Iterator[TickerMonthTrainingBatch]:
        if batch.sample_count <= 0:
            return
        self._chunks.append(batch)
        self._samples += int(batch.sample_count)
        while self._samples >= self.batch_size:
            combined = _concat_training_batches(self._chunks)
            yield _slice_training_batch(combined, 0, self.batch_size)
            remainder = _slice_training_batch(combined, self.batch_size, combined.sample_count)
            self._chunks = [remainder] if remainder.sample_count else []
            self._samples = int(remainder.sample_count)

    def flush(self) -> Iterator[TickerMonthTrainingBatch]:
        if self.drop_last or self._samples <= 0:
            self._chunks = []
            self._samples = 0
            return
        combined = _concat_training_batches(self._chunks)
        self._chunks = []
        self._samples = 0
        yield combined


def _concat_training_batches(batches: Sequence[TickerMonthTrainingBatch]) -> TickerMonthTrainingBatch:
    nonempty = [batch for batch in batches if batch.sample_count > 0]
    if not nonempty:
        return _empty_batch("")
    first = nonempty[0]
    raw_windows = _concat_dict_arrays(batch.raw_event_windows for batch in nonempty)
    raw_flat = _concat_dict_arrays(batch.raw_event_flat for batch in nonempty)
    intraday_labels = _concat_dict_arrays(batch.intraday_labels for batch in nonempty)
    availability = _concat_dict_arrays(batch.input_availability for batch in nonempty)
    profile = {
        "samples": sum(int(batch.sample_count) for batch in nonempty),
    }
    for batch in nonempty:
        for key, value in batch.profile.items():
            if key == "samples":
                continue
            if isinstance(value, (int, float)):
                profile[key] = float(profile.get(key, 0.0)) + float(value)
    return TickerMonthTrainingBatch(
        ticker=np.concatenate([batch.ticker for batch in nonempty], axis=0),
        origin_ordinal=np.concatenate([batch.origin_ordinal for batch in nonempty], axis=0),
        origin_timestamp_us=np.concatenate([batch.origin_timestamp_us for batch in nonempty], axis=0),
        event_output_mode=first.event_output_mode,
        source_part_key=np.concatenate([batch.source_part_key for batch in nonempty], axis=0),
        raw_event_windows=raw_windows,
        raw_event_flat=raw_flat,
        raw_event_stream=_concat_optional_arrays([batch.raw_event_stream for batch in nonempty]),
        raw_event_stream_feature_names=first.raw_event_stream_feature_names,
        raw_event_mask=_concat_optional_arrays([batch.raw_event_mask for batch in nonempty]),
        headers_uint8=_concat_optional_arrays([batch.headers_uint8 for batch in nonempty]),
        events_uint8=_concat_optional_arrays([batch.events_uint8 for batch in nonempty]),
        intraday_labels=intraday_labels,
        future_intraday_bar_horizons=first.future_intraday_bar_horizons,
        future_intraday_bars=_concat_optional_arrays([batch.future_intraday_bars for batch in nonempty]),
        future_intraday_bar_mask=_concat_optional_arrays([batch.future_intraday_bar_mask for batch in nonempty]),
        input_availability=availability,
        text_inputs=_concat_text_inputs(batch.text_inputs for batch in nonempty),
        external_context=_merge_external_context(nonempty),
        profile=profile,
    )


def _slice_training_batch(batch: TickerMonthTrainingBatch, start: int, end: int) -> TickerMonthTrainingBatch:
    start = max(0, int(start))
    end = max(start, min(int(end), int(batch.sample_count)))
    profile = _slice_profile(batch.profile, int(batch.sample_count), end - start)
    return TickerMonthTrainingBatch(
        ticker=batch.ticker[start:end],
        origin_ordinal=batch.origin_ordinal[start:end],
        origin_timestamp_us=batch.origin_timestamp_us[start:end],
        event_output_mode=batch.event_output_mode,
        source_part_key=batch.source_part_key[start:end] if batch.source_part_key.shape[0] else batch.source_part_key,
        raw_event_windows={key: value[start:end] for key, value in batch.raw_event_windows.items()},
        raw_event_flat={key: value[start:end] for key, value in batch.raw_event_flat.items()},
        raw_event_stream=batch.raw_event_stream[start:end] if batch.raw_event_stream.shape[0] else batch.raw_event_stream,
        raw_event_stream_feature_names=batch.raw_event_stream_feature_names,
        raw_event_mask=batch.raw_event_mask[start:end] if batch.raw_event_mask.shape[0] else batch.raw_event_mask,
        headers_uint8=batch.headers_uint8[start:end] if batch.headers_uint8.shape[0] else batch.headers_uint8,
        events_uint8=batch.events_uint8[start:end] if batch.events_uint8.shape[0] else batch.events_uint8,
        intraday_labels={key: value[start:end] for key, value in batch.intraday_labels.items()},
        future_intraday_bar_horizons=batch.future_intraday_bar_horizons,
        future_intraday_bars=batch.future_intraday_bars[start:end] if batch.future_intraday_bars.shape[0] else batch.future_intraday_bars,
        future_intraday_bar_mask=batch.future_intraday_bar_mask[start:end] if batch.future_intraday_bar_mask.shape[0] else batch.future_intraday_bar_mask,
        input_availability={key: value[start:end] for key, value in batch.input_availability.items()},
        text_inputs={name: {key: value[start:end] for key, value in payload.items()} for name, payload in batch.text_inputs.items()},
        external_context=dict(batch.external_context),
        profile=profile,
    )


def _concat_dict_arrays(items: Sequence[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    items = list(items)
    keys: set[str] = set()
    for item in items:
        keys.update(item.keys())
    return {key: np.concatenate([item[key] for item in items if key in item], axis=0) for key in sorted(keys)}


def _concat_text_inputs(items: Sequence[Mapping[str, Mapping[str, np.ndarray]]]) -> dict[str, dict[str, np.ndarray]]:
    items = list(items)
    names: set[str] = set()
    for item in items:
        names.update(item.keys())
    out: dict[str, dict[str, np.ndarray]] = {}
    for name in sorted(names):
        fields: set[str] = set()
        for item in items:
            if name in item:
                fields.update(item[name].keys())
        out[name] = {
            field: np.concatenate([item[name][field] for item in items if name in item and field in item[name]], axis=0)
            for field in sorted(fields)
        }
    return out


def _slice_profile(profile: Mapping[str, float | int], source_samples: int, output_samples: int) -> dict[str, float | int]:
    out: dict[str, float | int] = {"samples": int(output_samples)}
    if int(source_samples) <= 0:
        return out
    scale = float(output_samples) / float(source_samples)
    for key, value in profile.items():
        if key == "samples":
            continue
        if isinstance(value, (int, float)):
            out[key] = float(value) * scale
    return out


def _concat_optional_arrays(items: Sequence[np.ndarray]) -> np.ndarray:
    arrays = [item for item in items if getattr(item, "shape", (0,))[0] > 0]
    if not arrays:
        return items[0] if items else np.asarray([])
    return np.concatenate(arrays, axis=0)


def _merge_external_context(batches: Sequence[TickerMonthTrainingBatch]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for batch in batches:
        for key, value in batch.external_context.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            else:
                merged[key] = value
    return merged


def _count_batch_samples_for_part_keys(batch: TickerMonthTrainingBatch, part_keys: set[str]) -> int:
    if batch.source_part_key.shape[0] == 0 or not part_keys:
        return 0
    return int(np.count_nonzero(np.isin(batch.source_part_key.astype(str, copy=False), list(part_keys))))


def _materialize_bounded(
    executor: ThreadPoolExecutor,
    materializer: TickerMonthBatchMaterializer,
    loaded: Sequence[LoadedTickerMonthPart],
    batches: Iterator[list[TickerMonthSampleRef]],
    *,
    preserve_order: bool = True,
) -> Iterator[TickerMonthTrainingBatch]:
    max_pending = max(1, int(getattr(executor, "_max_workers", 1))) * 2
    if preserve_order:
        pending_ordered: deque[Future[TickerMonthTrainingBatch]] = deque()

        def submit_until_full_ordered() -> None:
            while len(pending_ordered) < max_pending:
                try:
                    refs = next(batches)
                except StopIteration:
                    return
                pending_ordered.append(executor.submit(materializer.materialize, loaded, refs))

        submit_until_full_ordered()
        while pending_ordered:
            future = pending_ordered.popleft()
            wait_start = time.perf_counter()
            batch = future.result()
            batch.profile["materialize_wait_seconds"] = float(batch.profile.get("materialize_wait_seconds", 0.0)) + (time.perf_counter() - wait_start)
            yield batch
            submit_until_full_ordered()
        return

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
        from concurrent.futures import FIRST_COMPLETED, wait

        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            wait_start = time.perf_counter()
            batch = future.result()
            batch.profile["materialize_wait_seconds"] = float(batch.profile.get("materialize_wait_seconds", 0.0)) + (time.perf_counter() - wait_start)
            yield batch
        submit_until_full()


def _identity_arrays(parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tickers = np.empty((len(refs),), dtype=object)
    ordinals = np.empty((len(refs),), dtype=np.int64)
    timestamps = np.empty((len(refs),), dtype=np.int64)
    for part_index, rows in _rows_by_part(refs).items():
        part = parts[int(part_index)]
        origin_rows = _origin_rows_for_refs(refs, rows)
        tickers[rows] = part.origin_array("ticker")[origin_rows]
        ordinals[rows] = part.origin_array("origin_ordinal").astype(np.int64, copy=False)[origin_rows]
        timestamps[rows] = part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[origin_rows]
    return tickers, ordinals, timestamps


def _source_part_keys(parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> np.ndarray:
    keys = np.empty((len(refs),), dtype=object)
    part_keys = [_part_key(part.plan) for part in parts]
    for part_index, rows in _rows_by_part(refs).items():
        keys[rows] = part_keys[int(part_index)]
    return keys


def _origin_event_offsets(parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> np.ndarray:
    offsets = np.empty((len(refs),), dtype=np.int64)
    for part_index, rows in _rows_by_part(refs).items():
        part = parts[int(part_index)]
        origin_rows = _origin_rows_for_refs(refs, rows)
        offsets[rows] = part.origin_array("event_row_offset").astype(np.int64, copy=False)[origin_rows]
    return offsets


def _rows_by_part(refs: Sequence[TickerMonthSampleRef]) -> dict[int, np.ndarray]:
    rows: dict[int, list[int]] = {}
    for row, ref in enumerate(refs):
        rows.setdefault(int(ref.part_index), []).append(int(row))
    return {part_index: np.asarray(part_rows, dtype=np.int64) for part_index, part_rows in rows.items()}


def _origin_rows_for_refs(refs: Sequence[TickerMonthSampleRef], rows: np.ndarray) -> np.ndarray:
    return np.fromiter((int(refs[int(row)].origin_row) for row in rows), dtype=np.int64, count=int(rows.shape[0]))


def _event_columns_for_output(config: TickerMonthLoaderConfig) -> tuple[str, ...]:
    if config.event_columns:
        return tuple(dict.fromkeys(str(column) for column in config.event_columns))
    suppressed = {str(column) for column in config.suppress_event_columns}
    return tuple(column for column in NUMERIC_EVENT_COLUMNS if column not in suppressed)


def _validated_event_columns_for_output(parts: Sequence[LoadedTickerMonthPart], config: TickerMonthLoaderConfig) -> tuple[str, ...]:
    columns = _event_columns_for_output(config)
    missing = [column for column in columns if not _all_parts_have_event_column(parts, column)]
    if missing and config.event_columns:
        raise RuntimeError(f"Requested event columns are missing from one or more loaded parts: {', '.join(missing)}")
    return tuple(column for column in columns if column not in missing)


def _all_parts_have_event_column(parts: Sequence[LoadedTickerMonthPart], column: str) -> bool:
    return all(part.events is not None and column in part.events.columns for part in parts)


def _event_column_dtype(parts: Sequence[LoadedTickerMonthPart], column: str) -> np.dtype:
    for part in parts:
        if part.events is not None and column in part.events.columns:
            return np.asarray(part.event_array(column)).dtype
    return np.float32


def _uses_dataset_hash_filter(config: TickerMonthLoaderConfig) -> bool:
    return (
        float(config.sample_fraction) < 1.0
        or int(config.sample_hash_modulus) > 0
        or bool(config.sample_hash_buckets)
    )


def _uses_fast_fraction_filter(config: TickerMonthLoaderConfig) -> bool:
    return (
        float(config.sample_fraction) < 1.0
        and int(config.sample_hash_modulus) <= 0
        and not bool(config.sample_hash_buckets)
    )


def _fast_fraction_candidate_rows(
    plan: TickerMonthPartPlan,
    candidate_rows: np.ndarray,
    *,
    config: TickerMonthLoaderConfig,
    seed: int,
    dataset_plan_id: str,
) -> np.ndarray:
    fraction = float(config.sample_fraction)
    if fraction <= 0.0 or candidate_rows.shape[0] <= 0:
        return np.asarray([], dtype=np.int64)
    if fraction >= 1.0:
        return candidate_rows.astype(np.int64, copy=False)
    rng_seed = _stable_int_seed("sample_fraction", dataset_plan_id, int(seed), plan.month, plan.ticker, int(plan.part_id))
    rng = np.random.default_rng(rng_seed)
    selected = rng.random(int(candidate_rows.shape[0])) < fraction
    return candidate_rows[selected].astype(np.int64, copy=False)


def _sample_selected(
    plan: TickerMonthPartPlan,
    origin_ordinal: int,
    origin_timestamp_us: int,
    *,
    config: TickerMonthLoaderConfig,
    seed: int,
    dataset_plan_id: str,
) -> bool:
    sample_hash = _sample_hash64(plan, origin_ordinal, origin_timestamp_us, seed=seed, dataset_plan_id=dataset_plan_id)
    fraction = float(config.sample_fraction)
    if fraction <= 0.0:
        return False
    if fraction < 1.0:
        threshold = int(max(0.0, min(1.0, fraction)) * float(2**64 - 1))
        if sample_hash >= threshold:
            return False
    modulus = int(config.sample_hash_modulus)
    if modulus > 0:
        buckets = {int(bucket) % modulus for bucket in config.sample_hash_buckets}
        if buckets and int(sample_hash % modulus) not in buckets:
            return False
    return True


def _sample_hash64(plan: TickerMonthPartPlan, origin_ordinal: int, origin_timestamp_us: int, *, seed: int, dataset_plan_id: str) -> int:
    text = "|".join(
        (
            str(dataset_plan_id),
            str(seed),
            str(plan.month),
            str(plan.ticker),
            str(plan.part_id),
            str(origin_ordinal),
            str(origin_timestamp_us),
        )
    )
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _stable_int_seed(*items: object) -> int:
    text = "|".join(str(item) for item in items)
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _cache_plan_fingerprint(manifest: Mapping[str, Any], parts: Sequence[TickerMonthPartPlan]) -> str:
    payload = {
        "format": str(manifest.get("format", "")),
        "version": int(manifest.get("version") or 0),
        "parts": [
            {
                "month": part.month,
                "ticker": part.ticker,
                "part_id": int(part.part_id),
                "origin_count": int(part.origin_count),
                "event_count": int(part.event_count),
                "label_count": int(part.label_count),
                "origin_ordinal_start": int(part.origin_ordinal_start),
                "origin_ordinal_end": int(part.origin_ordinal_end),
                "fetch_ordinal_start": int(part.fetch_ordinal_start),
                "fetch_ordinal_end": int(part.fetch_ordinal_end),
                "files": dict(sorted((str(key), str(value)) for key, value in part.files.items())),
            }
            for part in parts
        ],
    }
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dataset_plan_id(config: TickerMonthLoaderConfig, manifest_fingerprint: str) -> str:
    if config.dataset_id:
        return str(config.dataset_id)
    payload = {
        "cache_manifest_fingerprint": manifest_fingerprint,
        "split": config.split,
        "start_utc": config.start_utc,
        "end_utc": config.end_utc,
        "months": list(config.months),
        "tickers": list(config.tickers),
        "sample_fraction": float(config.sample_fraction),
        "sample_hash_modulus": int(config.sample_hash_modulus),
        "sample_hash_buckets": list(config.sample_hash_buckets),
        "max_origins_per_epoch": int(config.max_origins_per_epoch),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"auto_{digest[:16]}"


def _part_key(plan: TickerMonthPartPlan) -> str:
    return f"{plan.month}|{plan.ticker}|{int(plan.part_id)}"


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


def _gather_pivoted_label_column(column_values: np.ndarray, indices: np.ndarray, expected: int, dtype: np.dtype) -> np.ndarray:
    out = np.zeros((int(indices.shape[0]), int(expected)), dtype=dtype)
    if int(indices.shape[0]) <= 0:
        return out
    for row, cell in enumerate(column_values[indices]):
        values = _cell_array(cell)
        width = min(int(values.shape[0]), int(expected))
        if width:
            out[row, :width] = values[:width].astype(dtype, copy=False)
    return out


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
        source_part_key=np.asarray([], dtype=object),
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
