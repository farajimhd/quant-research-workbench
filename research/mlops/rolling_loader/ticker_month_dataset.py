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
    BAR_START_TIME_FEATURE_COLUMNS,
    CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    TICKER_MONTH_CACHE_FORMAT,
    TICKER_MONTH_CACHE_VERSION,
    full_months_in_period,
    month_dir_for,
    read_json,
    ticker_package_dir,
)


DEFAULT_EVENT_OUTPUT_MODE = "raw_stream"
SUPPORTED_EVENT_OUTPUT_MODES = {"none", "raw_flat", "raw_stream", "raw_windows", "encoded_uint8"}
DEFAULT_DATA_GROUPS = ("events", "intraday_labels")
TEXT_CONTEXT_GROUPS = {"ticker_news_embeddings", "market_news_embeddings", "sec_filing_embeddings"}
TEXT_CONTEXT_GROUP_ALIASES = {
    "ticker_news_tokens": "ticker_news_embeddings",
    "market_news_tokens": "market_news_embeddings",
    "sec_filing_tokens": "sec_filing_embeddings",
}
TEXT_INPUT_GROUP_TO_KEY = {
    "ticker_news_embeddings": "ticker_news",
    "market_news_embeddings": "market_news",
    "sec_filing_embeddings": "sec_filings",
}
XBRL_CONTEXT_GROUPS = {"xbrl"}
BAR_CONTEXT_GROUPS = {"daily_bars", "global_daily_bars"}
BAR_INPUT_GROUP_TO_KEY = {
    "daily_bars": "ticker_daily_bars",
    "global_daily_bars": "global_daily_bars",
}
BAR_FEATURE_KEYS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "dollar_volume",
    "trade_count",
    "quote_count",
    "vwap",
)
DEFAULT_TICKER_DAILY_BAR_OFFSETS = (1, 2, 3, 7, 14, 28, 40, 200)
DEFAULT_GLOBAL_DAILY_BAR_OFFSETS = (1, 2, 7)
DEFAULT_DAILY_BAR_COMPLETION_LAG_HOURS = 30.0
RELATIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "time_delta_seconds",
    "time_delta_seconds_log1p_signed",
    "time_age_seconds_log1p",
)
TEXT_ITEM_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, *RELATIVE_TIME_FEATURE_COLUMNS)
XBRL_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, *RELATIVE_TIME_FEATURE_COLUMNS)
XBRL_PERIOD_END_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "period_end_utc_day_of_week_sin",
    "period_end_utc_day_of_week_cos",
    "period_end_utc_day_of_year_sin",
    "period_end_utc_day_of_year_cos",
    "period_end_years_since_2000",
    "period_end_age_days",
    "period_end_age_days_log1p",
)
BAR_RELATIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = ("bar_age_days", "bar_age_days_log1p")
BAR_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*BAR_START_TIME_FEATURE_COLUMNS, *BAR_RELATIVE_TIME_FEATURE_COLUMNS)
METADATA_PAYLOAD_FIELDS = {"feature_names", "time_feature_names", "item_time_feature_names", "period_end_time_feature_names", "offsets", "symbols"}
LABEL_VALUE_DTYPES: dict[str, np.dtype] = {
    "price_primary_int": np.dtype(np.int32),
    "price_secondary_int": np.dtype(np.int32),
    "size_primary_sum": np.dtype(np.float32),
    "size_secondary_sum": np.dtype(np.float32),
    "event_count": np.dtype(np.uint64),
    "last_event_timestamp_us": np.dtype(np.int64),
    "available": np.dtype(np.bool_),
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
    xbrl_max_items: int = 4096
    ticker_news_token_chunks: int = 2
    market_news_token_chunks: int = 2
    sec_filing_token_chunks: int = 8
    text_max_tokens: int = 1024
    text_embedding_dim: int = 1024
    ticker_daily_bar_offsets: tuple[int, ...] = DEFAULT_TICKER_DAILY_BAR_OFFSETS
    global_daily_bar_offsets: tuple[int, ...] = DEFAULT_GLOBAL_DAILY_BAR_OFFSETS
    daily_bar_completion_lag_hours: float = DEFAULT_DAILY_BAR_COMPLETION_LAG_HOURS
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
    embeddings: np.ndarray
    chunk_mask: np.ndarray
    absolute_time_features: np.ndarray

    @property
    def item_count(self) -> int:
        return int(self.timestamps_us.shape[0])


@dataclass(slots=True)
class LabelContextIndex:
    ordinals: np.ndarray
    label_rows: np.ndarray
    missing_mask: np.ndarray

    @property
    def row_count(self) -> int:
        return int(self.ordinals.shape[0])


@dataclass(slots=True)
class DailyBarContextIndex:
    symbols: tuple[str, ...]
    bar_start_ms_by_symbol: dict[str, np.ndarray]
    values_by_symbol: dict[str, np.ndarray]
    time_features_by_symbol: dict[str, np.ndarray]


@dataclass(slots=True)
class XbrlCategoryReferenceIndex:
    ids_by_field_value: dict[tuple[str, str], int]

    def id(self, field_name: str, value: Any) -> int:
        if value is None:
            return 0
        return int(self.ids_by_field_value.get((str(field_name), str(value)), 0))


@dataclass(slots=True)
class XbrlContextIndex:
    timestamps_us: np.ndarray
    value: np.ndarray
    fiscal_year: np.ndarray
    period_end_days: np.ndarray
    fiscal_period_id: np.ndarray
    calendar_period_id: np.ndarray
    taxonomy_id: np.ndarray
    tag_id: np.ndarray
    unit_id: np.ndarray
    form_id: np.ndarray
    row_kind_id: np.ndarray
    location_id: np.ndarray
    mapping_confidence: np.ndarray
    absolute_time_features: np.ndarray
    period_end_time_features: np.ndarray

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
    xbrl_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    bar_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
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
        for package_dir in self._candidate_package_dirs(split_dir, selected_months, selected_tickers):
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

    def _candidate_package_dirs(self, split_dir: Path, selected_months: set[str], selected_tickers: set[str]) -> list[Path]:
        root = Path(self.config.cache_root)
        if selected_months and selected_tickers:
            paths: list[Path] = []
            for month in sorted(selected_months):
                month_dir = month_dir_for(root, str(self.config.split), month)
                for ticker in sorted(selected_tickers):
                    paths.append(ticker_package_dir(month_dir, ticker))
            return paths
        if selected_months:
            paths = []
            for month in sorted(selected_months):
                month_dir = month_dir_for(root, str(self.config.split), month)
                paths.extend(sorted(month_dir.glob("ticker_hash=*/ticker=*")))
            return paths
        if selected_tickers:
            paths = []
            for month_dir in sorted(split_dir.glob("month=*")):
                if not month_dir.is_dir():
                    continue
                for ticker in sorted(selected_tickers):
                    paths.append(ticker_package_dir(month_dir, ticker))
            return paths
        return sorted(split_dir.glob("month=*/ticker_hash=*/ticker=*"))


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
        if self.include_external_context or bool(TEXT_CONTEXT_GROUPS.union(BAR_CONTEXT_GROUPS).union(XBRL_CONTEXT_GROUPS).intersection(self.data_groups)):
            for key, filename in _package_context_files(plan.package_dir).items():
                if key in self.data_groups:
                    loaded.context[key] = pl.read_parquet(plan.package_dir / filename)
            if "market_news_embeddings" in self.data_groups:
                global_path = _month_global_dir(plan.package_dir) / "market_news_embeddings.parquet"
                cache_key = (global_path, "market_news_embeddings")
                if cache_key not in self._global_context_cache:
                    self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                loaded.context["market_news_embeddings"] = self._global_context_cache[cache_key]
            if "global_daily_bars" in self.data_groups:
                global_path = _month_global_dir(plan.package_dir) / "global_daily_bars.parquet"
                cache_key = (global_path, "global_daily_bars")
                if cache_key not in self._global_context_cache:
                    self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                loaded.context["global_daily_bars"] = self._global_context_cache[cache_key]
            if "xbrl" in self.data_groups:
                global_path = _month_global_dir(plan.package_dir) / "category_references.parquet"
                cache_key = (global_path, "category_references")
                if cache_key not in self._global_context_cache:
                    self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                loaded.context["category_references"] = self._global_context_cache[cache_key]
        return loaded


class TickerMonthBatchMaterializer:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.context_lags = tuple(index * int(self.config.context_stride_events) for index in range(int(self.config.context_chunks)))
        self._text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._global_text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._text_index_lock = threading.Lock()
        self._label_index_cache: dict[tuple[int, int, int], LabelContextIndex] = {}
        self._label_index_lock = threading.Lock()
        self._bar_index_cache: dict[int, DailyBarContextIndex] = {}
        self._bar_index_lock = threading.Lock()
        self._xbrl_index_cache: dict[tuple[int, int], XbrlContextIndex] = {}
        self._xbrl_index_lock = threading.Lock()
        self._xbrl_category_cache: dict[int, XbrlCategoryReferenceIndex] = {}
        self._xbrl_category_lock = threading.Lock()
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
        with self._label_index_lock:
            self._label_index_cache.clear()

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
        labels, future_bars, future_mask, horizons, label_profile = self._materialize_intraday_labels(parts, refs)
        profile.update(label_profile)
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
        xbrl_start = time.perf_counter()
        xbrl_inputs, xbrl_profile = self._materialize_xbrl_inputs(parts, refs)
        xbrl_mask = xbrl_inputs.get("mask")
        if xbrl_mask is not None:
            availability["xbrl_available"] = xbrl_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(xbrl_profile)
        profile["xbrl_seconds"] = time.perf_counter() - xbrl_start
        bar_start = time.perf_counter()
        bar_inputs, bar_profile = self._materialize_bar_inputs(parts, refs)
        for key, value in bar_inputs.items():
            bar_mask = value.get("mask")
            if bar_mask is not None:
                availability[f"{key}_available"] = bar_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(bar_profile)
        profile["bar_seconds"] = time.perf_counter() - bar_start
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
            xbrl_inputs=xbrl_inputs,
            bar_inputs=bar_inputs,
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
        requested = [group for group in ("ticker_news_embeddings", "market_news_embeddings", "sec_filing_embeddings") if group in self.config.data_groups]
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
            embedding_dim = int(self.config.text_embedding_dim)
            embeddings = np.zeros((len(refs), max_items, max_chunks, embedding_dim), dtype=np.float32)
            chunk_mask = np.zeros((len(refs), max_items, max_chunks), dtype=np.bool_)
            item_mask = np.zeros((len(refs), max_items), dtype=np.bool_)
            item_timestamp_us = np.zeros((len(refs), max_items), dtype=np.int64)
            item_time_features = np.zeros((len(refs), max_items, len(TEXT_ITEM_TIME_FEATURE_COLUMNS)), dtype=np.float32)
            if max_items <= 0:
                out[key] = {
                    "embeddings": embeddings,
                    "chunk_mask": chunk_mask,
                    "item_mask": item_mask,
                    "item_timestamp_us": item_timestamp_us,
                    "item_time_features": item_time_features,
                    "item_time_feature_names": np.asarray(TEXT_ITEM_TIME_FEATURE_COLUMNS, dtype=object),
                }
                continue
            for part_index, rows in grouped_rows.items():
                part = parts[int(part_index)]
                frame = part.context.get(group)
                if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                    continue
                index_start = time.perf_counter()
                index = self._text_context_index(frame, group, max_chunks=max_chunks, embedding_dim=embedding_dim)
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
                gathered_embeddings = index.embeddings[safe_indices]
                gathered_chunks = index.chunk_mask[safe_indices]
                gathered_timestamps = index.timestamps_us[safe_indices]
                gathered_absolute_time = index.absolute_time_features[safe_indices]
                origins = np.broadcast_to(origin_timestamps[rows, None], gathered_timestamps.shape)
                gathered_relative_time = _relative_time_feature_matrix(gathered_timestamps, origins)
                gathered_time_features = np.concatenate([gathered_absolute_time, gathered_relative_time], axis=-1).astype(np.float32, copy=False)
                invalid = ~selected_mask
                if bool(invalid.any()):
                    gathered_embeddings[invalid] = 0.0
                    gathered_chunks[invalid] = False
                    gathered_timestamps[invalid] = 0
                    gathered_time_features[invalid] = 0.0
                embeddings[rows] = gathered_embeddings
                chunk_mask[rows] = gathered_chunks
                item_mask[rows] = selected_mask
                item_timestamp_us[rows] = gathered_timestamps
                item_time_features[rows] = gathered_time_features
                profile["text_gather_seconds"] = float(profile["text_gather_seconds"]) + (time.perf_counter() - gather_start)
            out[key] = {
                "embeddings": embeddings,
                "chunk_mask": chunk_mask,
                "item_mask": item_mask,
                "item_timestamp_us": item_timestamp_us,
                "item_time_features": item_time_features,
                "item_time_feature_names": np.asarray(TEXT_ITEM_TIME_FEATURE_COLUMNS, dtype=object),
            }
        return out, profile

    def _text_context_index(self, frame: Any, group: str, *, max_chunks: int, embedding_dim: int) -> TextContextIndex:
        key = (id(frame), str(group), int(max_chunks), int(embedding_dim))
        cache = self._global_text_index_cache if str(group) == "market_news_embeddings" else self._text_index_cache
        with self._text_index_lock:
            cached = cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_text_context_index(frame, group, max_chunks=max_chunks, embedding_dim=embedding_dim)
            cache[key] = index
            return index

    def _materialize_xbrl_inputs(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "xbrl" not in self.config.data_groups:
            return {}, {}
        max_items = max(0, int(self.config.xbrl_max_items))
        batch = int(len(refs))
        shape = (batch, max_items)
        out: dict[str, np.ndarray] = {
            "mask": np.zeros(shape, dtype=np.bool_),
            "value": np.zeros(shape, dtype=np.float32),
            "fiscal_year": np.zeros(shape, dtype=np.int16),
            "age_days": np.zeros(shape, dtype=np.float32),
            "period_end_days": np.zeros(shape, dtype=np.int32),
            "fiscal_period_id": np.zeros(shape, dtype=np.uint32),
            "calendar_period_id": np.zeros(shape, dtype=np.uint32),
            "taxonomy_id": np.zeros(shape, dtype=np.uint32),
            "tag_id": np.zeros(shape, dtype=np.uint32),
            "unit_id": np.zeros(shape, dtype=np.uint32),
            "form_id": np.zeros(shape, dtype=np.uint32),
            "row_kind_id": np.zeros(shape, dtype=np.uint32),
            "location_id": np.zeros(shape, dtype=np.uint32),
            "mapping_confidence": np.zeros(shape, dtype=np.float32),
            "time_features": np.zeros((batch, max_items, len(XBRL_TIME_FEATURE_COLUMNS)), dtype=np.float32),
            "time_feature_names": np.asarray(XBRL_TIME_FEATURE_COLUMNS, dtype=object),
            "period_end_time_features": np.zeros((batch, max_items, len(XBRL_PERIOD_END_TIME_FEATURE_COLUMNS)), dtype=np.float32),
            "period_end_time_feature_names": np.asarray(XBRL_PERIOD_END_TIME_FEATURE_COLUMNS, dtype=object),
        }
        profile: dict[str, float | int] = {
            "xbrl_index_seconds": 0.0,
            "xbrl_select_seconds": 0.0,
            "xbrl_gather_seconds": 0.0,
            "xbrl_rows": int(batch),
            "xbrl_max_items": int(max_items),
        }
        time_feature_keys = (
            "time_delta_seconds",
            "time_delta_seconds_log1p_signed",
            "time_age_seconds_log1p",
            "time_utc_second_of_day_sin",
            "time_utc_second_of_day_cos",
            "time_utc_day_of_week_sin",
            "time_utc_day_of_week_cos",
            "time_utc_day_of_year_sin",
            "time_utc_day_of_year_cos",
            "time_years_since_2000",
        )
        for key in time_feature_keys:
            out[key] = np.zeros(shape, dtype=np.float32)
        if max_items <= 0 or batch <= 0:
            return out, profile
        origin_timestamps = _identity_arrays(parts, refs)[2]
        grouped_rows = _rows_by_part(refs)
        for part_index, rows in grouped_rows.items():
            part = parts[int(part_index)]
            frame = part.context.get("xbrl")
            if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                continue
            index_start = time.perf_counter()
            category_index = self._xbrl_category_index(part.context.get("category_references"))
            index = self._xbrl_context_index(frame, category_index, max_items=max_items)
            profile["xbrl_index_seconds"] = float(profile["xbrl_index_seconds"]) + (time.perf_counter() - index_start)
            if index.item_count <= 0:
                continue
            select_start = time.perf_counter()
            selected_indices, selected_mask = _select_xbrl_item_indices(index, origin_timestamps[rows], max_items=max_items)
            profile["xbrl_select_seconds"] = float(profile["xbrl_select_seconds"]) + (time.perf_counter() - select_start)
            if not bool(selected_mask.any()):
                continue
            gather_start = time.perf_counter()
            safe_indices = np.where(selected_mask, selected_indices, 0)
            out["mask"][rows] = selected_mask
            for key in ("value", "fiscal_year", "period_end_days", "fiscal_period_id", "calendar_period_id", "taxonomy_id", "tag_id", "unit_id", "form_id", "row_kind_id", "location_id", "mapping_confidence"):
                values = getattr(index, key)[safe_indices]
                values = values.astype(out[key].dtype, copy=False)
                values[~selected_mask] = 0
                out[key][rows] = values
            selected_timestamps = index.timestamps_us[safe_indices]
            selected_timestamps[~selected_mask] = 0
            origins = np.broadcast_to(origin_timestamps[rows, None], selected_timestamps.shape)
            timestamp_features = _timestamp_feature_arrays(selected_timestamps, origins)
            for key, values in timestamp_features.items():
                out[key][rows] = values.astype(np.float32, copy=False)
            relative_features = _relative_time_feature_matrix(selected_timestamps, origins)
            absolute_features = index.absolute_time_features[safe_indices]
            time_features = np.concatenate([absolute_features, relative_features], axis=-1).astype(np.float32, copy=False)
            time_features[~selected_mask] = 0.0
            out["time_features"][rows] = time_features
            period_absolute = index.period_end_time_features[safe_indices]
            origin_days = origin_timestamps[rows, None].astype(np.float64) / 86_400_000_000.0
            period_days = index.period_end_days[safe_indices].astype(np.float64)
            period_age_days = np.maximum(0.0, origin_days - period_days).astype(np.float32)
            period_relative = np.stack([period_age_days, np.log1p(period_age_days.astype(np.float64)).astype(np.float32)], axis=-1)
            period_features = np.concatenate([period_absolute, period_relative], axis=-1).astype(np.float32, copy=False)
            period_features[~selected_mask] = 0.0
            out["period_end_time_features"][rows] = period_features
            out["age_days"][rows] = np.maximum(
                0.0,
                (origin_timestamps[rows, None].astype(np.float64) - selected_timestamps.astype(np.float64)) / 86_400_000_000.0,
            ).astype(np.float32) * selected_mask
            profile["xbrl_gather_seconds"] = float(profile["xbrl_gather_seconds"]) + (time.perf_counter() - gather_start)
        return out, profile

    def _xbrl_context_index(self, frame: Any, category_index: XbrlCategoryReferenceIndex, *, max_items: int) -> XbrlContextIndex:
        key = (id(frame), int(max_items))
        with self._xbrl_index_lock:
            cached = self._xbrl_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_xbrl_context_index(frame, category_index)
            self._xbrl_index_cache[key] = index
            return index

    def _xbrl_category_index(self, frame: Any) -> XbrlCategoryReferenceIndex:
        key = id(frame)
        with self._xbrl_category_lock:
            cached = self._xbrl_category_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_xbrl_category_reference_index(frame)
            self._xbrl_category_cache[key] = index
            return index

    def _materialize_bar_inputs(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
        requested = [group for group in ("daily_bars", "global_daily_bars") if group in self.config.data_groups]
        if not requested:
            return {}, {}
        out: dict[str, dict[str, np.ndarray]] = {}
        profile: dict[str, float | int] = {
            "bar_index_seconds": 0.0,
            "bar_select_seconds": 0.0,
            "bar_gather_seconds": 0.0,
        }
        origin_timestamps_ms = _identity_arrays(parts, refs)[2] // 1000
        completion_lag_ms = int(max(0.0, float(self.config.daily_bar_completion_lag_hours)) * 3_600_000.0)
        cutoff_ms = origin_timestamps_ms - completion_lag_ms
        grouped_rows = _rows_by_part(refs)
        for group in requested:
            key = BAR_INPUT_GROUP_TO_KEY[group]
            offsets = np.asarray(_bar_offsets_for_group(self.config, group), dtype=np.int64)
            if group == "daily_bars":
                values = np.zeros((len(refs), int(offsets.shape[0]), len(BAR_FEATURE_KEYS)), dtype=np.float32)
                mask = np.zeros((len(refs), int(offsets.shape[0])), dtype=np.bool_)
                time_features = np.zeros((len(refs), int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
                for part_index, rows in grouped_rows.items():
                    part = parts[int(part_index)]
                    frame = part.context.get(group)
                    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                        continue
                    index_start = time.perf_counter()
                    index = self._daily_bar_context_index(frame)
                    profile["bar_index_seconds"] = float(profile["bar_index_seconds"]) + (time.perf_counter() - index_start)
                    symbol = str(part.plan.ticker).upper()
                    if symbol not in index.bar_start_ms_by_symbol:
                        continue
                    select_start = time.perf_counter()
                    selected, selected_mask = _select_completed_bar_rows(index.bar_start_ms_by_symbol[symbol], cutoff_ms[rows], offsets)
                    profile["bar_select_seconds"] = float(profile["bar_select_seconds"]) + (time.perf_counter() - select_start)
                    if not bool(selected_mask.any()):
                        continue
                    gather_start = time.perf_counter()
                    safe_selected = np.where(selected_mask, selected, 0)
                    gathered = index.values_by_symbol[symbol][safe_selected]
                    gathered[~selected_mask] = 0.0
                    selected_starts = index.bar_start_ms_by_symbol[symbol][safe_selected]
                    absolute_time = index.time_features_by_symbol[symbol][safe_selected]
                    age_days = np.maximum(0.0, (origin_timestamps_ms[rows, None].astype(np.float64) - selected_starts.astype(np.float64)) / 86_400_000.0).astype(np.float32)
                    relative_time = np.stack([age_days, np.log1p(age_days.astype(np.float64)).astype(np.float32)], axis=-1)
                    gathered_time = np.concatenate([absolute_time, relative_time], axis=-1).astype(np.float32, copy=False)
                    gathered_time[~selected_mask] = 0.0
                    values[rows] = gathered
                    mask[rows] = selected_mask
                    time_features[rows] = gathered_time
                    profile["bar_gather_seconds"] = float(profile["bar_gather_seconds"]) + (time.perf_counter() - gather_start)
                out[key] = {
                    "values": values,
                    "mask": mask,
                    "time_features": time_features,
                    "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                    "offsets": offsets.astype(np.int32, copy=False),
                    "feature_names": np.asarray(BAR_FEATURE_KEYS, dtype=object),
                }
                continue
            symbols: tuple[str, ...] = ()
            values = np.zeros((len(refs), 0, int(offsets.shape[0]), len(BAR_FEATURE_KEYS)), dtype=np.float32)
            mask = np.zeros((len(refs), 0, int(offsets.shape[0])), dtype=np.bool_)
            time_features = np.zeros((len(refs), 0, int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
            symbol_array = np.asarray([], dtype=object)
            for part_index, rows in grouped_rows.items():
                part = parts[int(part_index)]
                frame = part.context.get(group)
                if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                    continue
                index_start = time.perf_counter()
                index = self._daily_bar_context_index(frame)
                profile["bar_index_seconds"] = float(profile["bar_index_seconds"]) + (time.perf_counter() - index_start)
                if not symbols:
                    symbols = index.symbols
                    values = np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_FEATURE_KEYS)), dtype=np.float32)
                    mask = np.zeros((len(refs), len(symbols), int(offsets.shape[0])), dtype=np.bool_)
                    time_features = np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
                    symbol_array = np.asarray(symbols, dtype=object)
                gather_start = time.perf_counter()
                for symbol_index, symbol in enumerate(symbols):
                    if symbol not in index.bar_start_ms_by_symbol:
                        continue
                    selected, selected_mask = _select_completed_bar_rows(index.bar_start_ms_by_symbol[symbol], cutoff_ms[rows], offsets)
                    if not bool(selected_mask.any()):
                        continue
                    safe_selected = np.where(selected_mask, selected, 0)
                    gathered = index.values_by_symbol[symbol][safe_selected]
                    gathered[~selected_mask] = 0.0
                    selected_starts = index.bar_start_ms_by_symbol[symbol][safe_selected]
                    absolute_time = index.time_features_by_symbol[symbol][safe_selected]
                    age_days = np.maximum(0.0, (origin_timestamps_ms[rows, None].astype(np.float64) - selected_starts.astype(np.float64)) / 86_400_000.0).astype(np.float32)
                    relative_time = np.stack([age_days, np.log1p(age_days.astype(np.float64)).astype(np.float32)], axis=-1)
                    gathered_time = np.concatenate([absolute_time, relative_time], axis=-1).astype(np.float32, copy=False)
                    gathered_time[~selected_mask] = 0.0
                    values[rows, symbol_index] = gathered
                    mask[rows, symbol_index] = selected_mask
                    time_features[rows, symbol_index] = gathered_time
                profile["bar_gather_seconds"] = float(profile["bar_gather_seconds"]) + (time.perf_counter() - gather_start)
            out[key] = {
                "values": values,
                "mask": mask,
                "time_features": time_features,
                "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                "offsets": offsets.astype(np.int32, copy=False),
                "symbols": symbol_array,
                "feature_names": np.asarray(BAR_FEATURE_KEYS, dtype=object),
            }
        return out, profile

    def _daily_bar_context_index(self, frame: Any) -> DailyBarContextIndex:
        key = id(frame)
        with self._bar_index_lock:
            cached = self._bar_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_daily_bar_context_index(frame)
            self._bar_index_cache[key] = index
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
    ) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, tuple[str, ...], dict[str, float | int]]:
        if "intraday_labels" not in self.config.data_groups and "labels" not in self.config.data_groups:
            return {}, np.zeros((len(refs), 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32), np.zeros((len(refs), 0), dtype=np.bool_), (), {}
        horizons = _cached_horizons(parts)
        horizon_count = len(horizons)
        labels_out = {key: np.zeros((len(refs), horizon_count), dtype=dtype) for key, dtype in LABEL_VALUE_DTYPES.items()}
        bars = np.zeros((len(refs), horizon_count, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((len(refs), horizon_count), dtype=np.bool_)
        profile: dict[str, float | int] = {
            "label_index_seconds": 0.0,
            "label_lookup_seconds": 0.0,
            "label_gather_seconds": 0.0,
        }
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
                index_start = time.perf_counter()
                label_index = self._label_context_index(part, horizon_count)
                profile["label_index_seconds"] = float(profile["label_index_seconds"]) + (time.perf_counter() - index_start)
                lookup_start = time.perf_counter()
                max_origin_row = int(origin_rows.max()) if int(origin_rows.shape[0]) else -1
                if label_index.row_count < max_origin_row + 1:
                    raise RuntimeError(f"Origin/label index length mismatch for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
                missing = label_index.missing_mask[origin_rows]
                found = ~missing
                profile["label_lookup_seconds"] = float(profile["label_lookup_seconds"]) + (time.perf_counter() - lookup_start)
                if self.config.strict_audit and not bool(found.all()):
                    missing_pos = int(np.flatnonzero(~found)[0])
                    raise RuntimeError(f"Missing intraday labels for {part.plan.month}:{part.plan.ticker}|{int(origins[missing_pos])}.")
                if not np.any(found):
                    continue
                output_rows = rows[found]
                source_indices = label_index.label_rows[origin_rows[found]]
                gather_start = time.perf_counter()
                for key, out in labels_out.items():
                    out[output_rows] = _label_column_matrix_for_rows(part.labels, key, source_indices, horizon_count, out.dtype)
                profile["label_gather_seconds"] = float(profile["label_gather_seconds"]) + (time.perf_counter() - gather_start)
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
        return labels_out, bars, mask, horizons, profile

    def _label_context_index(self, part: LoadedTickerMonthPart, horizon_count: int) -> LabelContextIndex:
        key = (id(part.origins), id(part.labels), int(horizon_count))
        with self._label_index_lock:
            cached = self._label_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_label_context_index(part.origins, part.labels, int(horizon_count), strict=bool(self.config.strict_audit), part_key=_part_key(part.plan))
            self._label_index_cache[key] = index
            return index


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
    groups = tuple(dict.fromkeys(TEXT_CONTEXT_GROUP_ALIASES.get(str(group), str(group)) for group in config.data_groups))
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
        xbrl_max_items=max(0, int(config.xbrl_max_items)),
        ticker_news_token_chunks=max(1, int(config.ticker_news_token_chunks)),
        market_news_token_chunks=max(1, int(config.market_news_token_chunks)),
        sec_filing_token_chunks=max(1, int(config.sec_filing_token_chunks)),
        text_max_tokens=max(1, int(config.text_max_tokens)),
        text_embedding_dim=max(1, int(config.text_embedding_dim)),
        ticker_daily_bar_offsets=tuple(max(1, int(value)) for value in config.ticker_daily_bar_offsets),
        global_daily_bar_offsets=tuple(max(1, int(value)) for value in config.global_daily_bar_offsets),
        daily_bar_completion_lag_hours=max(0.0, float(config.daily_bar_completion_lag_hours)),
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
    out: dict[str, str] = {}
    for key, value in files.items():
        normalized = TEXT_CONTEXT_GROUP_ALIASES.get(str(key), str(key))
        if normalized in {"ticker_news_embeddings", "sec_filing_embeddings", "xbrl", "daily_bars"}:
            out[normalized] = str(value)
    return out


def _text_group_limits(config: TickerMonthLoaderConfig, group: str) -> tuple[int, int]:
    group = TEXT_CONTEXT_GROUP_ALIASES.get(str(group), str(group))
    if group == "ticker_news_embeddings":
        return max(0, int(config.ticker_news_max_items)), max(1, int(config.ticker_news_token_chunks))
    if group == "market_news_embeddings":
        return max(0, int(config.market_news_max_items)), max(1, int(config.market_news_token_chunks))
    if group == "sec_filing_embeddings":
        return max(0, int(config.sec_filing_max_items)), max(1, int(config.sec_filing_token_chunks))
    return 0, 1


def _prepare_text_context_index(frame: Any, group: str, *, max_chunks: int, embedding_dim: int) -> TextContextIndex:
    max_chunks = max(1, int(max_chunks))
    embedding_dim = max(1, int(embedding_dim))
    if int(getattr(frame, "height", 0) or 0) <= 0:
        return _empty_text_context_index(max_chunks=max_chunks, embedding_dim=embedding_dim)
    required = {"timestamp_us", "token_chunk_index", "embedding"}
    if not required.issubset(set(getattr(frame, "columns", ()))): 
        return _empty_text_context_index(max_chunks=max_chunks, embedding_dim=embedding_dim)
    group = TEXT_CONTEXT_GROUP_ALIASES.get(str(group), str(group))
    height = int(frame.height)
    timestamps = frame.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
    chunk_indices = frame.get_column("token_chunk_index").to_numpy()
    embedding_values = frame.get_column("embedding").to_numpy()
    row_time_features = _cached_or_computed_time_feature_matrix(frame, CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, timestamps)
    columns = set(frame.columns)
    source_id = _optional_frame_column(frame, "source_id", "")
    if group == "sec_filing_embeddings":
        accession_number = _optional_frame_column(frame, "accession_number", "")
        document_id = _optional_frame_column(frame, "document_id", "")
        text_rank = _optional_frame_column(frame, "text_rank", 0)
        grouped: dict[tuple[Any, ...], list[int]] = {}
        item_timestamp: dict[tuple[Any, ...], int] = {}
        item_time_features: dict[tuple[Any, ...], np.ndarray] = {}
        for row_index in range(height):
            key = (
                str(accession_number[row_index] if "accession_number" in columns else ""),
                str(document_id[row_index] if "document_id" in columns else ""),
                _safe_int(text_rank[row_index] if "text_rank" in columns else 0),
                str(source_id[row_index] if "source_id" in columns else ""),
            )
            grouped.setdefault(key, []).append(row_index)
            row_timestamp = int(timestamps[row_index])
            if row_timestamp >= int(item_timestamp.get(key, 0)):
                item_timestamp[key] = row_timestamp
                item_time_features[key] = row_time_features[row_index]
    else:
        provider_article_id = _optional_frame_column(frame, "provider_article_id", "")
        text_hash = _optional_frame_column(frame, "text_hash", "")
        grouped = {}
        item_timestamp = {}
        item_time_features = {}
        for row_index in range(height):
            key = (
                str(source_id[row_index] if "source_id" in columns else ""),
                str(provider_article_id[row_index] if "provider_article_id" in columns else ""),
                str(text_hash[row_index] if "text_hash" in columns else ""),
            )
            grouped.setdefault(key, []).append(row_index)
            row_timestamp = int(timestamps[row_index])
            if row_timestamp >= int(item_timestamp.get(key, 0)):
                item_timestamp[key] = row_timestamp
                item_time_features[key] = row_time_features[row_index]
    items = sorted(((int(item_timestamp[key]), key, rows) for key, rows in grouped.items()), key=lambda item: (item[0], item[1]))
    if not items:
        return _empty_text_context_index(max_chunks=max_chunks, embedding_dim=embedding_dim)
    out_timestamps = np.empty((len(items),), dtype=np.int64)
    out_embeddings = np.zeros((len(items), max_chunks, embedding_dim), dtype=np.float32)
    out_chunk_mask = np.zeros((len(items), max_chunks), dtype=np.bool_)
    out_time_features = np.zeros((len(items), len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)), dtype=np.float32)
    for item_index, (timestamp_us, _key, rows) in enumerate(items):
        out_timestamps[item_index] = int(timestamp_us)
        out_time_features[item_index] = item_time_features.get(_key, _absolute_utc_time_feature_matrix(np.asarray([timestamp_us], dtype=np.int64))[0])
        rows = sorted(rows, key=lambda row: _safe_int(chunk_indices[row]))
        for row_index in rows:
            chunk_index = _safe_int(chunk_indices[row_index])
            if chunk_index < 0 or chunk_index >= max_chunks:
                continue
            embedding = _cell_array(embedding_values[row_index]).astype(np.float32, copy=False)
            width = min(embedding_dim, int(embedding.shape[0]))
            if width <= 0:
                continue
            if not bool(np.isfinite(embedding[:width]).all()):
                raise RuntimeError(f"Text embedding context contains non-finite values for group={group!r}.")
            out_embeddings[item_index, chunk_index, :width] = embedding[:width]
            out_chunk_mask[item_index, chunk_index] = True
    return TextContextIndex(timestamps_us=out_timestamps, embeddings=out_embeddings, chunk_mask=out_chunk_mask, absolute_time_features=out_time_features)


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


def _empty_text_context_index(*, max_chunks: int, embedding_dim: int) -> TextContextIndex:
    return TextContextIndex(
        timestamps_us=np.zeros((0,), dtype=np.int64),
        embeddings=np.zeros((0, max(1, int(max_chunks)), max(1, int(embedding_dim))), dtype=np.float32),
        chunk_mask=np.zeros((0, max(1, int(max_chunks))), dtype=np.bool_),
        absolute_time_features=np.zeros((0, len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)), dtype=np.float32),
    )


def _prepare_xbrl_category_reference_index(frame: Any) -> XbrlCategoryReferenceIndex:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return XbrlCategoryReferenceIndex(ids_by_field_value={})
    columns = set(getattr(frame, "columns", ()))
    required = {"domain", "field_name", "category_value", "category_id"}
    if not required.issubset(columns):
        return XbrlCategoryReferenceIndex(ids_by_field_value={})
    domains = frame.get_column("domain").to_numpy()
    fields = frame.get_column("field_name").to_numpy()
    values = frame.get_column("category_value").to_numpy()
    ids = frame.get_column("category_id").to_numpy()
    out: dict[tuple[str, str], int] = {}
    for domain, field, value, category_id in zip(domains, fields, values, ids):
        if str(domain) != "xbrl":
            continue
        cid = _safe_int(category_id)
        if cid <= 0:
            continue
        out[(str(field), str(value))] = int(cid)
    return XbrlCategoryReferenceIndex(ids_by_field_value=out)


def _prepare_xbrl_context_index(frame: Any, category_index: XbrlCategoryReferenceIndex) -> XbrlContextIndex:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0 or "timestamp_us" not in getattr(frame, "columns", ()):
        return _empty_xbrl_context_index()
    height = int(frame.height)
    timestamps = _frame_column_as(frame, "timestamp_us", np.int64, 0)
    absolute_time_features = _cached_or_computed_time_feature_matrix(frame, CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, timestamps)
    order_keys: list[np.ndarray] = [
        _object_frame_column(frame, "period_end_date", ""),
        _object_frame_column(frame, "unit_code", ""),
        _object_frame_column(frame, "tag", ""),
        _object_frame_column(frame, "taxonomy", ""),
        _object_frame_column(frame, "xbrl_row_kind", ""),
        timestamps,
    ]
    order = np.lexsort(tuple(order_keys))
    timestamps = timestamps[order]
    absolute_time_features = absolute_time_features[order]
    value = _frame_column_as(frame, "value", np.float32, 0.0)[order]
    fiscal_year = _frame_column_as(frame, "fiscal_year", np.int16, 0)[order]
    period_end_days = _epoch_days_array(_optional_frame_column(frame, "period_end_date", ""))[order]
    period_end_time_features = _period_end_absolute_time_feature_matrix(period_end_days)
    fiscal_period_id = _map_category_ids(_optional_frame_column(frame, "fiscal_period", ""), "fiscal_period", category_index)[order]
    calendar_period_id = _map_category_ids(_optional_frame_column(frame, "calendar_period_code", ""), "calendar_period_code", category_index)[order]
    taxonomy_id = _map_category_ids(_optional_frame_column(frame, "taxonomy", ""), "taxonomy", category_index)[order]
    tag_id = _map_category_ids(_optional_frame_column(frame, "tag", ""), "tag", category_index)[order]
    unit_id = _map_category_ids(_optional_frame_column(frame, "unit_code", ""), "unit_code", category_index)[order]
    form_id = _map_category_ids(_optional_frame_column(frame, "form_type", ""), "form_type", category_index)[order]
    row_kind_id = _map_category_ids(_optional_frame_column(frame, "xbrl_row_kind", ""), "xbrl_row_kind", category_index)[order]
    location_id = _map_category_ids(_optional_frame_column(frame, "location_code", ""), "location_code", category_index)[order]
    mapping_confidence = _frame_column_as(frame, "mapping_confidence_score", np.float32, 0.0)[order]
    if int(timestamps.shape[0]) != height:
        raise RuntimeError("XBRL index row count changed while preparing context.")
    return XbrlContextIndex(
        timestamps_us=timestamps,
        value=value,
        fiscal_year=fiscal_year,
        period_end_days=period_end_days,
        fiscal_period_id=fiscal_period_id,
        calendar_period_id=calendar_period_id,
        taxonomy_id=taxonomy_id,
        tag_id=tag_id,
        unit_id=unit_id,
        form_id=form_id,
        row_kind_id=row_kind_id,
        location_id=location_id,
        mapping_confidence=mapping_confidence,
        absolute_time_features=absolute_time_features.astype(np.float32, copy=False),
        period_end_time_features=period_end_time_features.astype(np.float32, copy=False),
    )


def _empty_xbrl_context_index() -> XbrlContextIndex:
    return XbrlContextIndex(
        timestamps_us=np.zeros((0,), dtype=np.int64),
        value=np.zeros((0,), dtype=np.float32),
        fiscal_year=np.zeros((0,), dtype=np.int16),
        period_end_days=np.zeros((0,), dtype=np.int32),
        fiscal_period_id=np.zeros((0,), dtype=np.uint32),
        calendar_period_id=np.zeros((0,), dtype=np.uint32),
        taxonomy_id=np.zeros((0,), dtype=np.uint32),
        tag_id=np.zeros((0,), dtype=np.uint32),
        unit_id=np.zeros((0,), dtype=np.uint32),
        form_id=np.zeros((0,), dtype=np.uint32),
        row_kind_id=np.zeros((0,), dtype=np.uint32),
        location_id=np.zeros((0,), dtype=np.uint32),
        mapping_confidence=np.zeros((0,), dtype=np.float32),
        absolute_time_features=np.zeros((0, len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)), dtype=np.float32),
        period_end_time_features=np.zeros((0, len(XBRL_PERIOD_END_TIME_FEATURE_COLUMNS) - 2), dtype=np.float32),
    )


def _select_xbrl_item_indices(index: XbrlContextIndex, origin_timestamps_us: np.ndarray, *, max_items: int) -> tuple[np.ndarray, np.ndarray]:
    origins = np.asarray(origin_timestamps_us, dtype=np.int64)
    max_items = max(0, int(max_items))
    if max_items <= 0 or index.item_count <= 0 or origins.shape[0] <= 0:
        return np.full((int(origins.shape[0]), max_items), -1, dtype=np.int64), np.zeros((int(origins.shape[0]), max_items), dtype=np.bool_)
    rightmost = np.searchsorted(index.timestamps_us, origins, side="right") - 1
    offsets = np.arange(max_items, dtype=np.int64)
    indices = rightmost[:, None] - offsets[None, :]
    valid = indices >= 0
    return np.where(valid, indices, -1).astype(np.int64, copy=False), valid


def _map_category_ids(values: np.ndarray, field_name: str, category_index: XbrlCategoryReferenceIndex) -> np.ndarray:
    out = np.zeros((int(values.shape[0]),), dtype=np.uint32)
    mapping = category_index.ids_by_field_value
    if not mapping:
        return out
    for idx, value in enumerate(values):
        out[idx] = np.uint32(mapping.get((str(field_name), str(value)), 0))
    return out


def _frame_column_as(frame: Any, name: str, dtype: Any, default: Any) -> np.ndarray:
    if name not in getattr(frame, "columns", ()):
        return np.full((int(getattr(frame, "height", 0) or 0),), default, dtype=dtype)
    return frame.get_column(name).to_numpy().astype(dtype, copy=False)


def _object_frame_column(frame: Any, name: str, default: Any) -> np.ndarray:
    return _optional_frame_column(frame, name, default).astype(object, copy=False)


def _epoch_days_array(values: np.ndarray) -> np.ndarray:
    out = np.zeros((int(values.shape[0]),), dtype=np.int32)
    for idx, value in enumerate(values):
        out[idx] = np.int32(_date_to_epoch_day(value))
    return out


def _date_to_epoch_day(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, dt.datetime):
        return int(value.date().toordinal() - dt.date(1970, 1, 1).toordinal())
    if isinstance(value, dt.date):
        return int(value.toordinal() - dt.date(1970, 1, 1).toordinal())
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return 0
        return int((value.astype("datetime64[D]") - np.datetime64("1970-01-01", "D")).astype(np.int64))
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(dt.date.fromisoformat(text[:10]).toordinal() - dt.date(1970, 1, 1).toordinal())
    except ValueError:
        return 0


def _timestamp_feature_arrays(timestamps_us: np.ndarray, origins_us: np.ndarray) -> dict[str, np.ndarray]:
    source = np.asarray(timestamps_us, dtype=np.int64)
    origin = np.asarray(origins_us, dtype=np.int64)
    source, origin = np.broadcast_arrays(source, origin)
    valid = source > 0
    delta_us = np.where(valid, source - origin, 0).astype(np.int64, copy=False)
    delta_seconds = (delta_us.astype(np.float64) / 1_000_000.0).astype(np.float32)
    delta_seconds_log1p_signed = (
        np.sign(delta_seconds).astype(np.float32) * np.log1p(np.abs(delta_seconds).astype(np.float64)).astype(np.float32)
    )
    age_seconds_log1p = np.log1p(np.maximum(0.0, -delta_seconds.astype(np.float64))).astype(np.float32)
    source_dt = source.astype("datetime64[us]")
    source_day = source_dt.astype("datetime64[D]")
    seconds_of_day = ((source_dt - source_day).astype("timedelta64[us]").astype(np.float64) / 1_000_000.0).astype(np.float32)
    epoch_days = (source_day - np.datetime64("1970-01-01", "D")).astype(np.int64)
    day_of_week = ((epoch_days + 3) % 7).astype(np.float32)
    source_year = source_day.astype("datetime64[Y]")
    day_of_year = (source_day - source_year.astype("datetime64[D]")).astype(np.int64).astype(np.float32)
    years_since_2000 = ((source_dt - np.datetime64("2000-01-01T00:00:00", "us")).astype("timedelta64[us]").astype(np.float64) / (365.2425 * 86_400_000_000.0)).astype(np.float32)
    zeros = np.zeros(source.shape, dtype=np.float32)
    seconds_of_day = np.where(valid, seconds_of_day, zeros)
    day_of_week = np.where(valid, day_of_week, zeros)
    day_of_year = np.where(valid, day_of_year, zeros)
    years_since_2000 = np.where(valid, years_since_2000, zeros)
    return {
        "time_delta_seconds": delta_seconds,
        "time_delta_seconds_log1p_signed": delta_seconds_log1p_signed,
        "time_age_seconds_log1p": age_seconds_log1p,
        "time_utc_second_of_day_sin": np.sin(2.0 * np.pi * seconds_of_day / 86_400.0).astype(np.float32) * valid,
        "time_utc_second_of_day_cos": np.cos(2.0 * np.pi * seconds_of_day / 86_400.0).astype(np.float32) * valid,
        "time_utc_day_of_week_sin": np.sin(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
        "time_utc_day_of_week_cos": np.cos(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
        "time_utc_day_of_year_sin": np.sin(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
        "time_utc_day_of_year_cos": np.cos(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
        "time_years_since_2000": years_since_2000,
    }


def _absolute_utc_time_feature_matrix(timestamps_us: np.ndarray) -> np.ndarray:
    source = np.asarray(timestamps_us, dtype=np.int64)
    valid = source > 0
    source_dt = source.astype("datetime64[us]")
    source_day = source_dt.astype("datetime64[D]")
    seconds_of_day = ((source_dt - source_day).astype("timedelta64[us]").astype(np.float64) / 1_000_000.0).astype(np.float32)
    epoch_days = (source_day - np.datetime64("1970-01-01", "D")).astype(np.int64)
    day_of_week = ((epoch_days + 3) % 7).astype(np.float32)
    source_year = source_day.astype("datetime64[Y]")
    day_of_year = (source_day - source_year.astype("datetime64[D]")).astype(np.int64).astype(np.float32)
    years_since_2000 = ((source_dt - np.datetime64("2000-01-01T00:00:00", "us")).astype("timedelta64[us]").astype(np.float64) / (365.2425 * 86_400_000_000.0)).astype(np.float32)
    zeros = np.zeros(source.shape, dtype=np.float32)
    seconds_of_day = np.where(valid, seconds_of_day, zeros)
    day_of_week = np.where(valid, day_of_week, zeros)
    day_of_year = np.where(valid, day_of_year, zeros)
    years_since_2000 = np.where(valid, years_since_2000, zeros)
    return np.stack(
        [
            np.sin(2.0 * np.pi * seconds_of_day / 86_400.0).astype(np.float32) * valid,
            np.cos(2.0 * np.pi * seconds_of_day / 86_400.0).astype(np.float32) * valid,
            np.sin(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
            np.cos(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
            np.sin(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
            np.cos(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
            years_since_2000,
        ],
        axis=-1,
    ).astype(np.float32, copy=False)


def _relative_time_feature_matrix(timestamps_us: np.ndarray, origins_us: np.ndarray) -> np.ndarray:
    source = np.asarray(timestamps_us, dtype=np.int64)
    origin = np.asarray(origins_us, dtype=np.int64)
    source, origin = np.broadcast_arrays(source, origin)
    valid = source > 0
    delta_seconds = np.where(valid, source - origin, 0).astype(np.float64) / 1_000_000.0
    delta_seconds = delta_seconds.astype(np.float32)
    delta_log = np.sign(delta_seconds).astype(np.float32) * np.log1p(np.abs(delta_seconds).astype(np.float64)).astype(np.float32)
    age_log = np.log1p(np.maximum(0.0, -delta_seconds.astype(np.float64))).astype(np.float32)
    return np.stack([delta_seconds, delta_log, age_log], axis=-1).astype(np.float32, copy=False)


def _period_end_absolute_time_feature_matrix(period_end_days: np.ndarray) -> np.ndarray:
    days = np.asarray(period_end_days, dtype=np.int64)
    valid = days > 0
    source_day = np.datetime64("1970-01-01", "D") + days.astype("timedelta64[D]")
    epoch_days = (source_day - np.datetime64("1970-01-01", "D")).astype(np.int64)
    day_of_week = ((epoch_days + 3) % 7).astype(np.float32)
    source_year = source_day.astype("datetime64[Y]")
    day_of_year = (source_day - source_year.astype("datetime64[D]")).astype(np.int64).astype(np.float32)
    years_since_2000 = ((source_day - np.datetime64("2000-01-01", "D")).astype("timedelta64[D]").astype(np.float64) / 365.2425).astype(np.float32)
    zeros = np.zeros(days.shape, dtype=np.float32)
    day_of_week = np.where(valid, day_of_week, zeros)
    day_of_year = np.where(valid, day_of_year, zeros)
    years_since_2000 = np.where(valid, years_since_2000, zeros)
    return np.stack(
        [
            np.sin(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
            np.cos(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
            np.sin(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
            np.cos(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
            years_since_2000,
        ],
        axis=-1,
    ).astype(np.float32, copy=False)


def _cached_or_computed_time_feature_matrix(frame: Any, columns: Sequence[str], timestamps_us: np.ndarray) -> np.ndarray:
    column_list = tuple(str(column) for column in columns)
    if column_list and all(column in getattr(frame, "columns", ()) for column in column_list):
        return frame.select(list(column_list)).to_numpy().astype(np.float32, copy=False)
    return _absolute_utc_time_feature_matrix(timestamps_us)


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
    xbrl_inputs = _concat_dict_arrays(batch.xbrl_inputs for batch in nonempty)
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
        xbrl_inputs=xbrl_inputs,
        bar_inputs=_concat_bar_inputs(nonempty),
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
        text_inputs={name: {key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in payload.items()} for name, payload in batch.text_inputs.items()},
        xbrl_inputs={key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in batch.xbrl_inputs.items()},
        bar_inputs={name: {key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in payload.items()} for name, payload in batch.bar_inputs.items()},
        external_context=dict(batch.external_context),
        profile=profile,
    )


def _concat_dict_arrays(items: Sequence[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    items = list(items)
    keys: set[str] = set()
    for item in items:
        keys.update(item.keys())
    out: dict[str, np.ndarray] = {}
    for key in sorted(keys):
        values = [item[key] for item in items if key in item]
        out[key] = values[0] if key in METADATA_PAYLOAD_FIELDS else np.concatenate(values, axis=0)
    return out


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
            field: (
                [item[name][field] for item in items if name in item and field in item[name]][0]
                if field in METADATA_PAYLOAD_FIELDS
                else np.concatenate([item[name][field] for item in items if name in item and field in item[name]], axis=0)
            )
            for field in sorted(fields)
        }
    return out


def _concat_bar_inputs(batches: Sequence[TickerMonthTrainingBatch]) -> dict[str, dict[str, np.ndarray]]:
    names: set[str] = set()
    for batch in batches:
        names.update(batch.bar_inputs.keys())
    out: dict[str, dict[str, np.ndarray]] = {}
    for name in sorted(names):
        fields: set[str] = set()
        for batch in batches:
            if name in batch.bar_inputs:
                fields.update(batch.bar_inputs[name].keys())
        payload: dict[str, np.ndarray] = {}
        for field in sorted(fields):
            values = [batch.bar_inputs[name][field] for batch in batches if name in batch.bar_inputs and field in batch.bar_inputs[name]]
            if field in {"values", "mask", "time_features"}:
                payload[field] = np.concatenate(values, axis=0)
            else:
                payload[field] = values[0]
        out[name] = payload
    return out


def _slice_batch_payload_field(value: np.ndarray, start: int, end: int, sample_count: int) -> np.ndarray:
    if isinstance(value, np.ndarray) and value.shape[:1] and int(value.shape[0]) == int(sample_count):
        return value[start:end]
    return value


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


def _prepare_label_context_index(origins: Any, labels: Any, expected: int, *, strict: bool, part_key: str) -> LabelContextIndex:
    expected = max(0, int(expected))
    if origins is None or int(getattr(origins, "height", 0) or 0) <= 0:
        return _empty_label_context_index(expected)
    origin_ordinals = origins.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
    origin_count = int(origin_ordinals.shape[0])
    missing = np.ones((origin_count,), dtype=np.bool_)
    label_rows = np.full((origin_count,), -1, dtype=np.int64)
    if labels is None or int(getattr(labels, "height", 0) or 0) <= 0:
        if strict and origin_count:
            raise RuntimeError(f"Missing all intraday labels for {part_key}.")
        return LabelContextIndex(ordinals=origin_ordinals, label_rows=label_rows, missing_mask=missing)
    label_ordinals = labels.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
    if int(label_ordinals.shape[0]) == origin_count and bool(np.array_equal(label_ordinals, origin_ordinals)):
        label_rows = np.arange(origin_count, dtype=np.int64)
        missing[:] = False
        return LabelContextIndex(ordinals=origin_ordinals, label_rows=label_rows, missing_mask=missing)
    if label_ordinals.shape[0] > 1 and not bool(np.all(label_ordinals[1:] >= label_ordinals[:-1])):
        order = np.argsort(label_ordinals, kind="stable")
        sorted_ordinals = label_ordinals[order]
    else:
        order = np.arange(int(label_ordinals.shape[0]), dtype=np.int64)
        sorted_ordinals = label_ordinals
    if sorted_ordinals.shape[0] > 1:
        duplicate_positions = np.flatnonzero(sorted_ordinals[1:] == sorted_ordinals[:-1])
        if duplicate_positions.size:
            duplicate_ordinal = int(sorted_ordinals[int(duplicate_positions[0])])
            if bool(np.any(origin_ordinals == duplicate_ordinal)):
                raise RuntimeError(f"Duplicate intraday label rows for {part_key}|{duplicate_ordinal}.")
    positions = np.searchsorted(sorted_ordinals, origin_ordinals, side="left")
    in_bounds = positions < sorted_ordinals.shape[0]
    if np.any(in_bounds):
        valid_origin_rows = np.flatnonzero(in_bounds)
        matched = sorted_ordinals[positions[valid_origin_rows]] == origin_ordinals[valid_origin_rows]
        matched_origin_rows = valid_origin_rows[matched]
        if matched_origin_rows.size:
            label_rows[matched_origin_rows] = order[positions[matched_origin_rows]]
            missing[matched_origin_rows] = False
    if strict and bool(missing.any()):
        first_missing = int(np.flatnonzero(missing)[0])
        raise RuntimeError(f"Missing intraday labels for {part_key}|{int(origin_ordinals[first_missing])}.")
    return LabelContextIndex(ordinals=origin_ordinals, label_rows=label_rows, missing_mask=missing)


def _label_column_matrix(labels: Any, key: str, expected: int, dtype: np.dtype) -> np.ndarray:
    expected = max(0, int(expected))
    row_count = int(getattr(labels, "height", 0) or 0)
    out = np.zeros((row_count, expected), dtype=dtype)
    if row_count <= 0 or expected <= 0 or key not in getattr(labels, "columns", ()):
        return out
    series = labels.get_column(key)
    candidates: list[np.ndarray] = []
    try:
        direct = series.to_numpy()
        if isinstance(direct, np.ndarray):
            candidates.append(direct)
    except Exception:
        pass
    dtype_text = str(getattr(series, "dtype", "")).lower()
    if "list" in dtype_text:
        try:
            converted = series.list.to_array(expected).to_numpy()
            if isinstance(converted, np.ndarray):
                candidates.insert(0, converted)
        except Exception:
            pass
    for values in candidates:
        if values.ndim == 2 and values.shape[0] == row_count:
            width = min(expected, int(values.shape[1]))
            if width:
                out[:, :width] = values[:, :width].astype(dtype, copy=False)
            return out
        if values.ndim == 1 and values.shape[0] == row_count and values.dtype != object and expected == 1:
            out[:, 0] = values.astype(dtype, copy=False)
            return out
    return _gather_pivoted_label_column(series.to_numpy(), np.arange(row_count, dtype=np.int64), expected, dtype)


def _label_column_matrix_for_rows(labels: Any, key: str, rows: np.ndarray, expected: int, dtype: np.dtype) -> np.ndarray:
    expected = max(0, int(expected))
    rows = np.asarray(rows, dtype=np.int64)
    out = np.zeros((int(rows.shape[0]), expected), dtype=dtype)
    if int(rows.shape[0]) <= 0 or expected <= 0 or labels is None or key not in getattr(labels, "columns", ()):
        return out
    row_count = int(getattr(labels, "height", 0) or 0)
    if np.any(rows < 0) or np.any(rows >= row_count):
        raise RuntimeError(f"Label row selection is out of bounds for {key}.")
    if int(rows.shape[0]) == row_count and bool(np.array_equal(rows, np.arange(row_count, dtype=np.int64))):
        return _label_column_matrix(labels, key, expected, dtype)
    series = labels.get_column(key)
    selected = None
    try:
        selected = series.gather(rows)
    except Exception:
        try:
            selected = series.take(rows)
        except Exception:
            selected = None
    if selected is not None:
        dtype_text = str(getattr(selected, "dtype", "")).lower()
        if "list" in dtype_text:
            try:
                values = selected.list.to_array(expected).to_numpy()
                if isinstance(values, np.ndarray) and values.ndim == 2:
                    width = min(expected, int(values.shape[1]))
                    if width:
                        out[:, :width] = values[:, :width].astype(dtype, copy=False)
                    return out
            except Exception:
                pass
        try:
            values = selected.to_numpy()
            if isinstance(values, np.ndarray) and values.ndim == 2:
                width = min(expected, int(values.shape[1]))
                if width:
                    out[:, :width] = values[:, :width].astype(dtype, copy=False)
                return out
            if isinstance(values, np.ndarray) and values.ndim == 1 and values.dtype != object and expected == 1:
                out[:, 0] = values.astype(dtype, copy=False)
                return out
        except Exception:
            pass
    return _gather_pivoted_label_column(series.to_numpy(), rows, expected, dtype)


def _empty_label_context_index(expected: int) -> LabelContextIndex:
    expected = max(0, int(expected))
    return LabelContextIndex(
        ordinals=np.zeros((0,), dtype=np.int64),
        label_rows=np.zeros((0,), dtype=np.int64),
        missing_mask=np.zeros((0,), dtype=np.bool_),
    )


def _bar_offsets_for_group(config: TickerMonthLoaderConfig, group: str) -> tuple[int, ...]:
    raw = config.global_daily_bar_offsets if str(group) == "global_daily_bars" else config.ticker_daily_bar_offsets
    offsets = tuple(sorted({max(1, int(value)) for value in raw}))
    return offsets or (1,)


def _prepare_daily_bar_context_index(frame: Any) -> DailyBarContextIndex:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return DailyBarContextIndex(symbols=(), bar_start_ms_by_symbol={}, values_by_symbol={}, time_features_by_symbol={})
    missing = [column for column in ("sym", "bar_start_ms", *BAR_FEATURE_KEYS) if column not in getattr(frame, "columns", ())]
    if missing:
        raise RuntimeError(f"Daily bar context is missing required columns: {', '.join(missing)}")
    symbols_raw = frame.get_column("sym").to_numpy()
    symbols = np.asarray([str(symbol).upper() for symbol in symbols_raw], dtype=object)
    starts = frame.get_column("bar_start_ms").to_numpy().astype(np.int64, copy=False)
    values = frame.select(list(BAR_FEATURE_KEYS)).to_numpy().astype(np.float32, copy=False)
    time_features = _cached_or_computed_time_feature_matrix(frame, BAR_START_TIME_FEATURE_COLUMNS, starts.astype(np.int64, copy=False) * 1000)
    order = np.lexsort((starts, symbols))
    symbols = symbols[order]
    starts = starts[order]
    values = values[order]
    time_features = time_features[order]
    unique_symbols = tuple(str(symbol) for symbol in np.unique(symbols))
    starts_by_symbol: dict[str, np.ndarray] = {}
    values_by_symbol: dict[str, np.ndarray] = {}
    time_features_by_symbol: dict[str, np.ndarray] = {}
    for symbol in unique_symbols:
        rows = symbols == symbol
        starts_by_symbol[str(symbol)] = starts[rows]
        values_by_symbol[str(symbol)] = values[rows]
        time_features_by_symbol[str(symbol)] = time_features[rows]
    return DailyBarContextIndex(symbols=unique_symbols, bar_start_ms_by_symbol=starts_by_symbol, values_by_symbol=values_by_symbol, time_features_by_symbol=time_features_by_symbol)


def _select_completed_bar_rows(bar_start_ms: np.ndarray, cutoff_ms: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    bar_start_ms = np.asarray(bar_start_ms, dtype=np.int64)
    cutoff_ms = np.asarray(cutoff_ms, dtype=np.int64)
    offsets = np.asarray(offsets, dtype=np.int64)
    completed_counts = np.searchsorted(bar_start_ms, cutoff_ms, side="right")
    rows = completed_counts[:, None] - offsets[None, :]
    mask = rows >= 0
    safe_rows = np.where(mask, rows, 0).astype(np.int64, copy=False)
    return safe_rows, mask


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
