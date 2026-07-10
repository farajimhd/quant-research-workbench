from __future__ import annotations

import datetime as dt
import gc
import heapq
import hashlib
import json
import math
import random
import secrets
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.clickhouse_events import encode_unified_event_windows
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES
from research.mlops.data.contracts import BAR_FAMILY_FEATURE_KEYS, BAR_FAMILY_KEYS, FUTURE_BAR_FEATURE_KEYS
from research.mlops.rolling_loader.daily_index_cache import (
    BAR_END_TIME_FEATURE_COLUMNS,
    BAR_START_TIME_FEATURE_COLUMNS,
    CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS,
    CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    DAILY_INDEX_CACHE_FORMAT,
    DAILY_INDEX_CACHE_VERSION,
    full_months_in_period,
    read_json,
    ticker_from_path_token,
    ticker_path_token,
)


DEFAULT_EVENT_OUTPUT_MODE = "raw_stream"
DEFAULT_INTRADAY_LABEL_HORIZONS = (
    "100ms",
    "200ms",
    "300ms",
    "400ms",
    "500ms",
    "1s",
    "2s",
    "3s",
    "5s",
    "10s",
    "15s",
    "30s",
    "60s",
    "120s",
    "180s",
    "300s",
    "600s",
    "900s",
    "1200s",
    "1800s",
    "3600s",
    "7200s",
    "3h",
    "4h",
    "5h",
    "eod",
)
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
CORPORATE_ACTION_CONTEXT_GROUPS = {"corporate_actions"}
CORPORATE_ACTION_LABEL_GROUPS = {"corporate_action_labels", "corporate_action_daily_labels"}
BAR_CONTEXT_GROUPS = {"daily_bars", "global_daily_bars"}
INTRADAY_BAR_CONTEXT_GROUPS = {"intraday_bars"}
BAR_INPUT_GROUP_TO_KEY = {
    "daily_bars": "ticker_daily_bars",
    "global_daily_bars": "global_daily_bars",
    "intraday_bars": "ticker_intraday_bars",
}
SCANNER_CONTEXT_GROUPS = {"scanner_context"}
DEFAULT_SCANNER_GROUPS = (
    "top_gainers",
    "top_volume_large_cap",
    "top_volume_mid_cap",
    "top_volume_small_cap",
    "top_volume_penny",
)
DEFAULT_SCANNER_HORIZONS = ("1s", "5s", "30s", "1m")
SCANNER_NUMERIC_FEATURE_KEYS = (
    "rank_score",
    "rank_percentile",
    "origin_is_leader",
    "origin_topk_position",
    "origin_rank",
    "origin_rank_percentile",
)
BAR_FEATURE_KEYS: tuple[str, ...] = (
    "open",
    "close",
    "high",
    "low",
    "size_sum",
    "size_open",
    "size_close",
    "size_high",
    "size_low",
    "event_count",
)
TRADE_BAR_FEATURE_KEYS: tuple[str, ...] = BAR_FAMILY_FEATURE_KEYS["trade"]
QUOTE_BAR_FEATURE_KEYS: tuple[str, ...] = BAR_FAMILY_FEATURE_KEYS["quote_bid"]
BAR_SOURCE_FEATURE_KEYS: dict[str, tuple[str, ...]] = dict(BAR_FAMILY_FEATURE_KEYS)
BAR_SOURCE_FEATURE_INDEX: dict[str, int] = {name: index for index, name in enumerate(BAR_FEATURE_KEYS)}
BAR_SOURCE_FEATURE_COLUMNS: dict[str, tuple[int, ...]] = {
    family: tuple(BAR_SOURCE_FEATURE_INDEX[name] for name in fields)
    for family, fields in BAR_SOURCE_FEATURE_KEYS.items()
}
FUTURE_BAR_VALUE_DTYPES: dict[str, np.dtype] = {
    f"{family}_{field}": np.dtype(np.float32)
    for family, fields in BAR_SOURCE_FEATURE_KEYS.items()
    for field in fields
}
FUTURE_BAR_VALUE_DTYPES.update({f"{family}_available": np.dtype(np.bool_) for family in BAR_FAMILY_KEYS})
FUTURE_BAR_VALUE_DTYPES.update({f"{family}_last_event_timestamp_us": np.dtype(np.int64) for family in BAR_FAMILY_KEYS})
DEFAULT_TICKER_DAILY_BAR_OFFSETS = (1, 2, 3, 7, 14, 28, 40, 200)
DEFAULT_GLOBAL_DAILY_BAR_OFFSETS = (1, 2, 7)
DEFAULT_DAILY_BAR_COMPLETION_LAG_HOURS = 30.0
SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60
SESSION_END_US = SESSION_END_SECOND * 1_000_000
SESSION_LENGTH_US = (SESSION_END_SECOND - SESSION_START_SECOND) * 1_000_000
INTRADAY_LABEL_GRID_RESOLUTIONS_US: tuple[int, ...] = (100_000, 1_000_000, 5_000_000, 30_000_000, 60_000_000)
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
CORPORATE_ACTION_NUMERIC_FEATURE_KEYS: tuple[str, ...] = (
    "split_from",
    "split_to",
    "share_factor",
    "price_factor",
    "log_share_factor",
    "log_price_factor",
    "cash_amount",
    "log1p_cash_amount",
    "is_split",
    "is_forward_split",
    "is_reverse_split",
    "is_dividend",
    "is_special_dividend",
)
CORPORATE_ACTION_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, *RELATIVE_TIME_FEATURE_COLUMNS)
CORPORATE_ACTION_EFFECTIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS, *RELATIVE_TIME_FEATURE_COLUMNS)
CORPORATE_ACTION_LABEL_DTYPES: dict[str, np.dtype] = {
    "future_split_flag": np.dtype(np.bool_),
    "future_reverse_split_flag": np.dtype(np.bool_),
    "future_forward_split_flag": np.dtype(np.bool_),
    "future_dividend_ex_flag": np.dtype(np.bool_),
    "future_special_dividend_ex_flag": np.dtype(np.bool_),
    "future_any_corporate_action_flag": np.dtype(np.bool_),
}
BAR_RELATIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = ("bar_age_days", "bar_age_days_log1p")
BAR_TIME_FEATURE_COLUMNS: tuple[str, ...] = (*BAR_START_TIME_FEATURE_COLUMNS, *BAR_RELATIVE_TIME_FEATURE_COLUMNS)
BAR_END_RELATIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = ("bar_end_age_days", "bar_end_age_days_log1p")
BAR_END_FEATURE_COLUMNS: tuple[str, ...] = (*BAR_END_TIME_FEATURE_COLUMNS, *BAR_END_RELATIVE_TIME_FEATURE_COLUMNS)
METADATA_PAYLOAD_FIELDS = {
    "feature_names",
    "numeric_feature_names",
    "time_feature_names",
    "start_time_feature_names",
    "end_time_feature_names",
    "item_time_feature_names",
    "period_end_time_feature_names",
    "effective_time_feature_names",
    "trade_feature_names",
    "quote_bid_feature_names",
    "quote_ask_feature_names",
    "offsets",
    "symbols",
    "group_names",
    "horizons",
    "family_names",
}
LABEL_VALUE_DTYPES: dict[str, np.dtype] = {
    "label_resolution_us": np.dtype(np.uint64),
    "label_grid_start_timestamp_us": np.dtype(np.int64),
    "label_grid_end_timestamp_us": np.dtype(np.int64),
    "size_primary_sum": np.dtype(np.float32),
    "size_secondary_sum": np.dtype(np.float32),
    "event_count": np.dtype(np.uint64),
    "last_event_timestamp_us": np.dtype(np.int64),
    "available": np.dtype(np.bool_),
    "condition_halt_pause_flag": np.dtype(np.bool_),
    "condition_resume_flag": np.dtype(np.bool_),
    "condition_news_risk_flag": np.dtype(np.bool_),
    "condition_luld_limit_state_flag": np.dtype(np.bool_),
    "ticker_news_arrival_flag": np.dtype(np.bool_),
    "sec_filing_arrival_flag": np.dtype(np.bool_),
}
LABEL_VALUE_DTYPES.update(FUTURE_BAR_VALUE_DTYPES)
NUMERIC_EVENT_COLUMNS: tuple[str, ...] = tuple(column for column in (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS) if column != "ticker")
DEFAULT_SUPPRESSED_EVENT_COLUMNS = ("ticker_id", "ordinal", "timestamp_us")
LOADER_STATE_VERSION = 1
ENCODER_EVENT_DTYPE = np.dtype(
    [
        ("ordinal", "<u8"),
        ("event_meta", "u1"),
        ("sip_timestamp_us", "<u8"),
        ("price_primary_int", "<u4"),
        ("price_secondary_int", "<u4"),
        ("size_primary", "<f4"),
        ("size_secondary", "<f4"),
        ("exchange_primary", "u1"),
        ("exchange_secondary", "u1"),
        ("condition_token_1", "u1"),
        ("condition_token_2", "u1"),
        ("condition_token_3", "u1"),
        ("condition_token_4", "u1"),
        ("condition_token_5", "u1"),
    ]
)


@dataclass(frozen=True, slots=True)
class DailyIndexLoaderConfig:
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
    loaded_parts_per_group: int = 256
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
    corporate_action_max_items: int = 128
    corporate_action_label_days: tuple[int, ...] = (1, 2, 3, 7, 28)
    intraday_label_horizons: tuple[str, ...] = DEFAULT_INTRADAY_LABEL_HORIZONS
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
    validate_time_feature_contract: bool = True
    scanner_groups: tuple[str, ...] = DEFAULT_SCANNER_GROUPS
    scanner_horizons: tuple[str, ...] = DEFAULT_SCANNER_HORIZONS
    scanner_top_k: int = 5
    scanner_required: bool = False
    scanner_index_cache_entries: int = 4
    prefetch_scanner_indexes: bool = True
    scanner_prefetch_workers: int = 4
    days: tuple[str, ...] = ()
    chronological_replay: bool = False
    time_window_seconds: float = 60.0
    frontier_max_origins_per_window: int = 0
    ticker_cache_capacity: int = 15_000
    origin_cursor_chunk_rows: int = 1024
    warm_all_ticker_caches: bool = True


@dataclass(frozen=True, slots=True)
class DailyIndexPartPlan:
    month: str
    ticker: str
    cache_root: Path
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
    source_date: str = ""
    origin_timestamp_start_us: int = 0
    origin_timestamp_end_us: int = 0


@dataclass(slots=True)
class LoadedDailyIndexPart:
    plan: DailyIndexPartPlan
    events: Any | None = None
    origins: Any | None = None
    windows: Any | None = None
    labels: Any | None = None
    context: dict[str, Any] = field(default_factory=dict)
    context_paths: dict[str, Path] = field(default_factory=dict)
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
class IntradayBarFamilyIndex:
    buckets: np.ndarray
    open: np.ndarray
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    size_sum: np.ndarray
    size_open: np.ndarray
    size_close: np.ndarray
    size_high: np.ndarray
    size_low: np.ndarray
    event_count: np.ndarray
    last_event_timestamp_us: np.ndarray
    cum_size_sum: np.ndarray
    cum_event_count: np.ndarray


@dataclass(slots=True)
class IntradayCompactLabelIndex:
    bars: dict[tuple[str, int, str], IntradayBarFamilyIndex]
    condition_events: dict[tuple[str, str], tuple[np.ndarray, dict[str, np.ndarray]]]
    ticker_news_by_date: dict[str, np.ndarray]
    sec_filing_by_date: dict[str, np.ndarray]


@dataclass(slots=True)
class ScannerArtifactIndex:
    source_date: np.ndarray
    bucket: np.ndarray
    ticker: np.ndarray
    ticker_id: np.ndarray
    timestamp_us: np.ndarray
    row_by_key: dict[tuple[str, int, str], int]
    leaders_by_key: dict[tuple[str, int, str], np.ndarray]
    columns: dict[str, np.ndarray]


@dataclass(slots=True)
class DailyBarContextIndex:
    symbols: tuple[str, ...]
    families: tuple[str, ...]
    bar_start_ms_by_family_symbol: dict[str, dict[str, np.ndarray]]
    values_by_family_symbol: dict[str, dict[str, np.ndarray]]
    start_time_features_by_family_symbol: dict[str, dict[str, np.ndarray]]
    end_time_features_by_family_symbol: dict[str, dict[str, np.ndarray]]


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


@dataclass(slots=True)
class CorporateActionContextIndex:
    available_timestamps_us: np.ndarray
    effective_timestamps_us: np.ndarray
    action_type_id: np.ndarray
    dividend_type_id: np.ndarray
    currency_id: np.ndarray
    frequency_id: np.ndarray
    numeric_features: np.ndarray
    available_time_features: np.ndarray
    effective_time_features: np.ndarray
    effective_epoch_day: np.ndarray
    declaration_epoch_day: np.ndarray
    pay_epoch_day: np.ndarray
    record_epoch_day: np.ndarray

    @property
    def item_count(self) -> int:
        return int(self.available_timestamps_us.shape[0])


@dataclass(frozen=True, slots=True)
class DailyIndexSampleRef:
    part_index: int
    origin_row: int


@dataclass(slots=True)
class DailyIndexTrainingBatch:
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
    corporate_action_labels: dict[str, np.ndarray] = field(default_factory=dict)
    corporate_action_label_days: tuple[int, ...] = ()
    future_intraday_bar_horizons: tuple[str, ...] = ()
    future_bar_values: dict[str, np.ndarray] = field(default_factory=dict)
    future_bar_masks: dict[str, np.ndarray] = field(default_factory=dict)
    future_bar_feature_names: dict[str, np.ndarray] = field(default_factory=dict)
    # Deprecated compatibility projection. Daily-index v3 uses family-specific
    # future_bar_values["trade"|"quote_bid"|"quote_ask"] instead.
    future_intraday_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32))
    future_intraday_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    input_availability: dict[str, np.ndarray] = field(default_factory=dict)
    scanner_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    text_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    xbrl_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    corporate_action_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    bar_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    external_context: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, float | int] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return int(self.origin_ordinal.shape[0])


@dataclass(slots=True)
class DailyIndexLoaderState:
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
    chronological_day_position: int = 0
    chronological_origin_cursor: int = 0
    chronological_window_start_us: int = 0
    chronological_frontier_after_timestamp_us: int = 0
    chronological_frontier_after_ticker: str = ""
    chronological_frontier_after_ordinal: int = 0
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
            "chronological_day_position": int(self.chronological_day_position),
            "chronological_origin_cursor": int(self.chronological_origin_cursor),
            "chronological_window_start_us": int(self.chronological_window_start_us),
            "chronological_frontier_after_timestamp_us": int(self.chronological_frontier_after_timestamp_us),
            "chronological_frontier_after_ticker": str(self.chronological_frontier_after_ticker),
            "chronological_frontier_after_ordinal": int(self.chronological_frontier_after_ordinal),
            "seen_by_month": dict(self.seen_by_month),
            "seen_by_part": dict(self.seen_by_part),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DailyIndexLoaderState":
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
            "chronological_day_position",
            "chronological_origin_cursor",
            "chronological_window_start_us",
            "chronological_frontier_after_timestamp_us",
            "chronological_frontier_after_ordinal",
        ):
            if key in value:
                setattr(state, key, int(value[key] or 0))
        state.dataset_plan_id = str(value.get("dataset_plan_id") or "")
        state.cache_manifest_fingerprint = str(value.get("cache_manifest_fingerprint") or "")
        state.chronological_frontier_after_ticker = str(value.get("chronological_frontier_after_ticker") or "")
        state.seen_by_month = {str(k): int(v or 0) for k, v in dict(value.get("seen_by_month") or {}).items()}
        state.seen_by_part = {str(k): int(v or 0) for k, v in dict(value.get("seen_by_part") or {}).items()}
        return state


class DailyIndexCacheIndex:
    def __init__(self, config: DailyIndexLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.root_manifest = _read_root_manifest(Path(self.config.cache_root))
        self.parts = self._discover_parts()

    def _discover_parts(self) -> list[DailyIndexPartPlan]:
        root = Path(self.config.cache_root)
        if not root.exists():
            raise FileNotFoundError(f"Missing daily-index cache root: {root}")
        selected_months = set(_selected_months(self.config, self.root_manifest))
        selected_tickers = {str(ticker) for ticker in self.config.tickers}
        selected_days = {str(day)[:10] for day in self.config.days if str(day).strip()}
        plans: list[DailyIndexPartPlan] = []
        for package_dir in self._candidate_package_dirs(root, selected_months, selected_tickers):
            if not package_dir.is_dir():
                continue
            month = _path_value(package_dir, "month")
            if selected_months and month not in selected_months:
                continue
            manifest_path = package_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = read_json(manifest_path)
            ticker = str(manifest.get("ticker") or ticker_from_path_token(_path_value(package_dir, "ticker")))
            if selected_tickers and ticker not in selected_tickers:
                continue
            package_config = {
                "event_context_rows": self.root_manifest.get("event_context_rows", 0),
                "event_context_guard_rows": self.root_manifest.get("event_context_guard_rows", 0),
                "event_context_year_lookback": self.root_manifest.get("event_context_year_lookback", 0),
                **(manifest.get("config") or {}),
            }
            package_files = _package_context_files_from_manifest(manifest)
            intraday_files_by_date = _intraday_context_files_by_source_date(manifest)
            for part in manifest.get("parts") or ():
                if not isinstance(part, Mapping):
                    continue
                origin_count = int(part.get("origin_rows") or 0)
                if origin_count <= 0:
                    continue
                source_date = _source_date_from_part(part)
                if selected_days and source_date not in selected_days:
                    continue
                files = {
                    **package_files,
                    **intraday_files_by_date.get(source_date, {}),
                    "events": str(part.get("event_path") or ""),
                    "origins": str(part.get("origin_path") or ""),
                }
                plans.append(
                    DailyIndexPartPlan(
                        month=str(month),
                        ticker=str(ticker),
                        cache_root=root,
                        package_dir=package_dir,
                        part_id=int(part.get("part_id") or 0),
                        files={str(key): str(value) for key, value in files.items()},
                        config=package_config,
                        origin_count=origin_count,
                        event_count=int(part.get("event_rows") or 0),
                        label_count=0,
                        origin_ordinal_start=int(part.get("ordinal_min") or 0),
                        origin_ordinal_end=int(part.get("ordinal_max") or 0),
                        fetch_ordinal_start=int(part.get("ordinal_min") or 0),
                        fetch_ordinal_end=int(part.get("ordinal_max") or 0),
                        source_date=str(source_date),
                        origin_timestamp_start_us=int(part.get("origin_timestamp_min_us") or part.get("timestamp_min_us") or 0),
                        origin_timestamp_end_us=int(part.get("origin_timestamp_max_us") or part.get("timestamp_max_us") or 0),
                    )
                )
        if not plans:
            raise RuntimeError(f"No complete daily-index event parts found under {root}")
        return plans

    def _candidate_package_dirs(self, root: Path, selected_months: set[str], selected_tickers: set[str]) -> list[Path]:
        root = Path(self.config.cache_root)
        if selected_months and selected_tickers:
            paths: list[Path] = []
            for month in sorted(selected_months):
                for ticker in sorted(selected_tickers):
                    paths.append(root / f"month={month}" / f"ticker={ticker_path_token(ticker)}")
                    paths.append(root / f"month={month}" / f"ticker={ticker}")
            return paths
        if selected_months:
            paths = []
            for month in sorted(selected_months):
                month_dir = root / f"month={month}"
                paths.extend(sorted(month_dir.glob("ticker=*")))
            return paths
        if selected_tickers:
            paths = []
            for month_dir in sorted(root.glob("month=*")):
                if not month_dir.is_dir():
                    continue
                for ticker in sorted(selected_tickers):
                    paths.append(month_dir / f"ticker={ticker_path_token(ticker)}")
                    paths.append(month_dir / f"ticker={ticker}")
            return paths
        return sorted(root.glob("month=*/ticker=*"))


class DailyIndexPartReader:
    def __init__(self, data_groups: Sequence[str], *, include_external_context: bool = False) -> None:
        self.data_groups = set(str(group) for group in data_groups)
        self.include_external_context = bool(include_external_context)
        self._global_context_cache: dict[tuple[Path, str], Any] = {}
        self._global_context_cache_order: deque[tuple[Path, str]] = deque()
        self._global_context_cache_lock = threading.Lock()
        self._scanner_context_cache_limit = 2

    def load(self, plan: DailyIndexPartPlan) -> LoadedDailyIndexPart:
        return self.load_payload(self.load_origins(plan))

    def load_origins(self, plan: DailyIndexPartPlan) -> LoadedDailyIndexPart:
        pl = _polars()
        loaded = LoadedDailyIndexPart(plan=plan)
        loaded.origins = pl.read_parquet(_plan_file_path(plan, plan.files["origins"]))
        return loaded

    def load_payload(
        self,
        loaded: LoadedDailyIndexPart,
        *,
        load_events: bool | None = None,
        load_labels: bool | None = None,
        load_intraday_bars: bool | None = None,
        load_corporate_labels: bool | None = None,
    ) -> LoadedDailyIndexPart:
        pl = _polars()
        plan = loaded.plan
        need_events = bool({"events", "event_windows", "encoded_events"}.intersection(self.data_groups)) if load_events is None else bool(load_events)
        need_labels = ("intraday_labels" in self.data_groups or "labels" in self.data_groups) if load_labels is None else bool(load_labels)
        need_intraday_bars = "intraday_bars" in self.data_groups if load_intraday_bars is None else bool(load_intraday_bars)
        need_corporate_labels = bool(CORPORATE_ACTION_LABEL_GROUPS.intersection(self.data_groups)) if load_corporate_labels is None else bool(load_corporate_labels)
        if need_events:
            loaded.events = pl.read_parquet(_plan_file_path(plan, plan.files["events"]))
        if need_events and "event_window_index" in plan.files:
            loaded.windows = pl.read_parquet(_plan_file_path(plan, plan.files["event_window_index"]))
        if need_labels and "intraday_forward_labels" in plan.files:
            loaded.labels = pl.read_parquet(_plan_file_path(plan, plan.files["intraday_forward_labels"]))
        if need_labels or need_intraday_bars:
            compact_files = {**_package_context_files(plan.package_dir), **dict(plan.files)}
            for key in ("intraday_base_bars", "intraday_condition_events", "ticker_news_embeddings", "sec_filing_embeddings"):
                filename = compact_files.get(key)
                if filename and key not in loaded.context:
                    loaded.context[key] = pl.read_parquet(_plan_file_path(plan, filename))
        if need_intraday_bars and "intraday_context_bars" in plan.files:
            loaded.context["intraday_bars"] = pl.read_parquet(_plan_file_path(plan, plan.files["intraday_context_bars"]))
        if need_corporate_labels and "corporate_action_daily_labels" in plan.files:
            loaded.context["corporate_action_daily_labels"] = pl.read_parquet(_plan_file_path(plan, plan.files["corporate_action_daily_labels"]))
        if self.include_external_context or bool(
            TEXT_CONTEXT_GROUPS.union(BAR_CONTEXT_GROUPS)
            .union(INTRADAY_BAR_CONTEXT_GROUPS)
            .union(XBRL_CONTEXT_GROUPS)
            .union(CORPORATE_ACTION_CONTEXT_GROUPS)
            .union(SCANNER_CONTEXT_GROUPS)
            .intersection(self.data_groups)
        ):
            for key, filename in _package_context_files(plan.package_dir).items():
                if key in self.data_groups:
                    loaded.context[key] = pl.read_parquet(_plan_file_path(plan, filename))
            if "market_news_embeddings" in self.data_groups:
                global_path = _global_context_file(_month_global_dir(plan.package_dir), "market_news_embeddings")
                cache_key = (global_path, "market_news_embeddings")
                with self._global_context_cache_lock:
                    if cache_key not in self._global_context_cache:
                        self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                    loaded.context["market_news_embeddings"] = self._global_context_cache[cache_key]
            if "global_daily_bars" in self.data_groups:
                global_path = _global_context_file(_month_global_dir(plan.package_dir), "global_daily_bars")
                cache_key = (global_path, "global_daily_bars")
                with self._global_context_cache_lock:
                    if cache_key not in self._global_context_cache:
                        self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                    loaded.context["global_daily_bars"] = self._global_context_cache[cache_key]
            if "scanner_context" in self.data_groups:
                scanner_path = _scanner_context_file(_month_global_dir(plan.package_dir), str(plan.source_date))
                cache_key = (scanner_path, "scanner_context")
                with self._global_context_cache_lock:
                    if cache_key not in self._global_context_cache:
                        self._global_context_cache[cache_key] = pl.read_parquet(scanner_path) if scanner_path.exists() else pl.DataFrame()
                        self._remember_global_context_cache_key(cache_key)
                    loaded.context["scanner_context"] = self._global_context_cache[cache_key]
                loaded.context_paths["scanner_context"] = scanner_path
            if "xbrl" in self.data_groups:
                global_path = _month_global_dir(plan.package_dir) / "category_references.parquet"
                cache_key = (global_path, "category_references")
                with self._global_context_cache_lock:
                    if cache_key not in self._global_context_cache:
                        self._global_context_cache[cache_key] = pl.read_parquet(global_path) if global_path.exists() else pl.DataFrame()
                    loaded.context["category_references"] = self._global_context_cache[cache_key]
        return loaded

    def _remember_global_context_cache_key(self, cache_key: tuple[Path, str]) -> None:
        if cache_key[1] != "scanner_context":
            return
        self._global_context_cache_order.append(cache_key)
        while len(self._global_context_cache_order) > max(1, int(self._scanner_context_cache_limit)):
            old_key = self._global_context_cache_order.popleft()
            if old_key != cache_key:
                self._global_context_cache.pop(old_key, None)


class DailyIndexBatchMaterializer:
    _shared_scanner_artifact_index_cache: OrderedDict[str, ScannerArtifactIndex] = OrderedDict()
    _shared_scanner_artifact_index_lock = threading.Lock()

    def __init__(self, config: DailyIndexLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.context_lags = tuple(index * int(self.config.context_stride_events) for index in range(int(self.config.context_chunks)))
        self._text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._global_text_index_cache: dict[tuple[int, str, int, int], TextContextIndex] = {}
        self._text_index_lock = threading.Lock()
        self._label_index_cache: dict[tuple[int, int, int], LabelContextIndex] = {}
        self._label_index_lock = threading.Lock()
        self._intraday_compact_label_cache: dict[int, IntradayCompactLabelIndex] = {}
        self._intraday_compact_label_lock = threading.Lock()
        self._scanner_artifact_index_cache: OrderedDict[str, ScannerArtifactIndex] = OrderedDict()
        self._scanner_artifact_index_lock = threading.Lock()
        self._bar_index_cache: dict[int, DailyBarContextIndex] = {}
        self._bar_index_lock = threading.Lock()
        self._xbrl_index_cache: dict[tuple[int, int], XbrlContextIndex] = {}
        self._xbrl_index_lock = threading.Lock()
        self._xbrl_category_cache: dict[int, XbrlCategoryReferenceIndex] = {}
        self._xbrl_category_lock = threading.Lock()
        self._corporate_action_index_cache: dict[tuple[int, int], CorporateActionContextIndex] = {}
        self._corporate_action_index_lock = threading.Lock()
        self.coverage_events = max(self.context_lags, default=0) + int(self.config.events_per_window)
        if self.config.event_output_mode == "raw_stream":
            self.coverage_events = int(self.config.event_stream_length)
        if int(self.config.flat_coverage_events) > 0:
            self.coverage_events = max(int(self.config.flat_coverage_events), int(self.coverage_events))

    def validate_part_config(self, plan: DailyIndexPartPlan) -> None:
        cached = int(
            plan.config.get("max_cached_event_lookback_rows")
            or plan.config.get("required_event_lookback_rows")
            or plan.config.get("event_context_rows")
            or 0
        )
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
            self._global_text_index_cache.clear()
        with self._label_index_lock:
            self._label_index_cache.clear()
            self._intraday_compact_label_cache.clear()
        with self._scanner_artifact_index_lock:
            self._scanner_artifact_index_cache.clear()
        with self._bar_index_lock:
            self._bar_index_cache.clear()
        with self._xbrl_index_lock:
            self._xbrl_index_cache.clear()
            self._xbrl_category_cache.clear()
        with self._corporate_action_index_lock:
            self._corporate_action_index_cache.clear()

    def has_scanner_artifact_index(self, cache_key: str) -> bool:
        key = str(cache_key)
        with self._scanner_artifact_index_lock:
            if key in self._scanner_artifact_index_cache:
                return True
        with self._shared_scanner_artifact_index_lock:
            return key in self._shared_scanner_artifact_index_cache

    def telemetry_snapshot(self) -> dict[str, int]:
        with self._text_index_lock:
            text_indexes = len(self._text_index_cache) + len(self._global_text_index_cache)
        with self._label_index_lock:
            label_indexes = len(self._label_index_cache) + len(self._intraday_compact_label_cache)
        with self._scanner_artifact_index_lock:
            scanner_indexes = len(self._scanner_artifact_index_cache)
        with self._bar_index_lock:
            bar_indexes = len(self._bar_index_cache)
        with self._xbrl_index_lock:
            xbrl_indexes = len(self._xbrl_index_cache)
            xbrl_categories = len(self._xbrl_category_cache)
        with self._corporate_action_index_lock:
            corporate_indexes = len(self._corporate_action_index_cache)
        return {
            "materializer_text_index_cache_entries": int(text_indexes),
            "materializer_label_index_cache_entries": int(label_indexes),
            "materializer_scanner_index_cache_entries": int(scanner_indexes),
            "materializer_bar_index_cache_entries": int(bar_indexes),
            "materializer_xbrl_index_cache_entries": int(xbrl_indexes),
            "materializer_xbrl_category_cache_entries": int(xbrl_categories),
            "materializer_corporate_action_index_cache_entries": int(corporate_indexes),
        }

    def materialize(
        self,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        raw_stream_override: np.ndarray | None = None,
        raw_stream_feature_names_override: Sequence[str] | None = None,
        profile_override: Mapping[str, float | int] | None = None,
        context_override: "_RollingContextBatch | None" = None,
    ) -> DailyIndexTrainingBatch:
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
            if raw_stream_override is not None:
                raw_stream = np.asarray(raw_stream_override, dtype=np.float32)
                raw_stream_feature_names = tuple(str(value) for value in (raw_stream_feature_names_override or ()))
                raw_stream_profile = dict(profile_override or {})
                raw_stream_profile.setdefault("raw_stream_rows", int(len(refs)))
                raw_stream_profile.setdefault("raw_stream_length", int(raw_stream.shape[1]) if raw_stream.ndim == 3 else 0)
                raw_stream_profile.setdefault("raw_stream_feature_count", int(raw_stream.shape[2]) if raw_stream.ndim == 3 else 0)
                raw_stream_profile.setdefault("raw_stream_rolling_override", int(1))
            else:
                raw_stream, raw_stream_feature_names, raw_stream_profile = self._materialize_raw_stream(parts, refs)
            raw_mask = np.ones((len(refs), int(raw_stream.shape[1])), dtype=np.bool_) if raw_stream.ndim == 3 else np.zeros((len(refs), 0), dtype=np.bool_)
            profile.update(raw_stream_profile)
        elif output_mode == "encoded_uint8":
            headers, encoded_events = self._materialize_encoded(parts, refs)
        profile["event_seconds"] = time.perf_counter() - event_start
        label_start = time.perf_counter()
        labels, future_bar_values, future_bar_masks, future_bar_feature_names, future_bars, future_mask, horizons, label_profile = self._materialize_intraday_labels(parts, refs)
        profile.update(label_profile)
        profile["label_seconds"] = time.perf_counter() - label_start
        corporate_label_start = time.perf_counter()
        corporate_labels, corporate_label_days, corporate_label_profile = self._materialize_corporate_action_labels(parts, refs)
        profile.update(corporate_label_profile)
        profile["corporate_action_label_seconds"] = time.perf_counter() - corporate_label_start
        availability = {
            "event_context_available": np.ones((len(refs),), dtype=np.bool_) if output_mode != "none" else np.zeros((len(refs),), dtype=np.bool_),
            "intraday_labels_available": future_mask.any(axis=1) if future_mask.size else np.zeros((len(refs),), dtype=np.bool_),
        }
        if corporate_labels:
            label_arrays = [
                value
                for value in corporate_labels.values()
                if isinstance(value, np.ndarray) and value.ndim == 2 and int(value.shape[0]) == int(len(refs))
            ]
            labels_valid = bool(label_arrays) and all(int(value.shape[1]) > 0 for value in label_arrays)
            availability["corporate_action_labels_available"] = (
                np.ones((len(refs),), dtype=np.bool_) if labels_valid else np.zeros((len(refs),), dtype=np.bool_)
            )
        text_start = time.perf_counter()
        if context_override is not None and context_override.text_inputs is not None:
            text_inputs = context_override.text_inputs
            text_profile = dict(context_override.profile)
            text_profile["text_rolling_override"] = int(1)
        else:
            text_inputs, text_profile = self._materialize_text_inputs(parts, refs)
        for key, value in text_inputs.items():
            chunk_mask = value.get("chunk_mask")
            if chunk_mask is not None:
                availability[f"{key}_available"] = chunk_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(text_profile)
        profile["text_seconds"] = time.perf_counter() - text_start
        xbrl_start = time.perf_counter()
        if context_override is not None and context_override.xbrl_inputs is not None:
            xbrl_inputs = context_override.xbrl_inputs
            xbrl_profile = {"xbrl_rolling_override": int(1)}
        else:
            xbrl_inputs, xbrl_profile = self._materialize_xbrl_inputs(parts, refs)
        xbrl_mask = xbrl_inputs.get("mask")
        if xbrl_mask is not None:
            availability["xbrl_available"] = xbrl_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(xbrl_profile)
        profile["xbrl_seconds"] = time.perf_counter() - xbrl_start
        bar_start = time.perf_counter()
        if context_override is not None and context_override.bar_inputs is not None:
            bar_inputs = context_override.bar_inputs
            bar_profile = {"bar_rolling_override": int(1)}
            intraday_bar_profile: dict[str, float | int] = {"intraday_bar_rolling_override": int(1)}
        else:
            bar_inputs, bar_profile = self._materialize_bar_inputs(parts, refs)
            intraday_bar_inputs, intraday_bar_profile = self._materialize_intraday_bar_inputs(parts, refs)
            bar_inputs.update(intraday_bar_inputs)
        for key, value in bar_inputs.items():
            bar_mask = value.get("mask")
            if bar_mask is not None:
                availability[f"{key}_available"] = bar_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(bar_profile)
        profile.update(intraday_bar_profile)
        profile["bar_seconds"] = time.perf_counter() - bar_start
        corporate_start = time.perf_counter()
        if context_override is not None and context_override.corporate_action_inputs is not None:
            corporate_inputs = context_override.corporate_action_inputs
            corporate_profile = {"corporate_action_rolling_override": int(1)}
        else:
            corporate_inputs, corporate_profile = self._materialize_corporate_action_inputs(parts, refs)
        corporate_mask = corporate_inputs.get("mask")
        if corporate_mask is not None:
            availability["corporate_actions_available"] = corporate_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(corporate_profile)
        profile["corporate_action_seconds"] = time.perf_counter() - corporate_start
        scanner_start = time.perf_counter()
        if context_override is not None and context_override.scanner_inputs is not None:
            scanner_inputs = context_override.scanner_inputs
            scanner_profile = {"scanner_rolling_override": int(1)}
        else:
            scanner_inputs, scanner_profile = self._materialize_scanner_inputs(parts, refs)
        scanner_mask = scanner_inputs.get("leader_mask")
        origin_mask = scanner_inputs.get("origin_mask")
        if scanner_mask is not None:
            availability["scanner_context_available"] = scanner_mask.reshape((len(refs), -1)).any(axis=1)
        elif origin_mask is not None:
            availability["scanner_context_available"] = origin_mask.reshape((len(refs), -1)).any(axis=1)
        profile.update(scanner_profile)
        profile["scanner_seconds"] = time.perf_counter() - scanner_start
        external_context = {}
        context_start = time.perf_counter()
        if self.config.include_external_context:
            external_context = _external_context_summary(parts)
        profile["context_seconds"] = time.perf_counter() - context_start
        profile["materialize_seconds"] = time.perf_counter() - start
        if self.config.validate_time_feature_contract:
            _validate_materialized_time_feature_contract(
                raw_stream=raw_stream,
                raw_stream_feature_names=raw_stream_feature_names,
                text_inputs=text_inputs,
                xbrl_inputs=xbrl_inputs,
                corporate_action_inputs=corporate_inputs,
                bar_inputs=bar_inputs,
                scanner_inputs=scanner_inputs,
            )
        return DailyIndexTrainingBatch(
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
            corporate_action_labels=corporate_labels,
            corporate_action_label_days=corporate_label_days,
            future_intraday_bar_horizons=horizons,
            future_bar_values=future_bar_values,
            future_bar_masks=future_bar_masks,
            future_bar_feature_names=future_bar_feature_names,
            future_intraday_bars=future_bars,
            future_intraday_bar_mask=future_mask,
            input_availability=availability,
            scanner_inputs=scanner_inputs,
            text_inputs=text_inputs,
            xbrl_inputs=xbrl_inputs,
            corporate_action_inputs=corporate_inputs,
            bar_inputs=bar_inputs,
            external_context=external_context,
            profile=profile,
        )

    def _materialize_raw_windows(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> dict[str, np.ndarray]:
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
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
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

    def _materialize_raw_flat(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, np.ndarray], np.ndarray]:
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

    def _materialize_text_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
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

    def _materialize_xbrl_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "xbrl" not in self.config.data_groups:
            return {}, {}
        max_items = max(0, int(self.config.xbrl_max_items))
        batch = int(len(refs))
        shape = (batch, max_items)
        out: dict[str, np.ndarray] = {
            "mask": np.zeros(shape, dtype=np.bool_),
            "timestamp_us": np.zeros(shape, dtype=np.int64),
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
            out["timestamp_us"][rows] = selected_timestamps
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

    def _materialize_corporate_action_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "corporate_actions" not in self.config.data_groups:
            return {}, {}
        max_items = max(0, int(self.config.corporate_action_max_items))
        batch = int(len(refs))
        shape = (batch, max_items)
        out: dict[str, np.ndarray] = {
            "mask": np.zeros(shape, dtype=np.bool_),
            "action_type_id": np.zeros(shape, dtype=np.uint32),
            "dividend_type_id": np.zeros(shape, dtype=np.uint32),
            "currency_id": np.zeros(shape, dtype=np.uint32),
            "frequency_id": np.zeros(shape, dtype=np.uint32),
            "available_timestamp_us": np.zeros(shape, dtype=np.int64),
            "effective_timestamp_us": np.zeros(shape, dtype=np.int64),
            "effective_epoch_day": np.zeros(shape, dtype=np.int32),
            "declaration_epoch_day": np.zeros(shape, dtype=np.int32),
            "pay_epoch_day": np.zeros(shape, dtype=np.int32),
            "record_epoch_day": np.zeros(shape, dtype=np.int32),
            "numeric_features": np.zeros((batch, max_items, len(CORPORATE_ACTION_NUMERIC_FEATURE_KEYS)), dtype=np.float32),
            "numeric_feature_names": np.asarray(CORPORATE_ACTION_NUMERIC_FEATURE_KEYS, dtype=object),
            "time_features": np.zeros((batch, max_items, len(CORPORATE_ACTION_TIME_FEATURE_COLUMNS)), dtype=np.float32),
            "time_feature_names": np.asarray(CORPORATE_ACTION_TIME_FEATURE_COLUMNS, dtype=object),
            "effective_time_features": np.zeros((batch, max_items, len(CORPORATE_ACTION_EFFECTIVE_TIME_FEATURE_COLUMNS)), dtype=np.float32),
            "effective_time_feature_names": np.asarray(CORPORATE_ACTION_EFFECTIVE_TIME_FEATURE_COLUMNS, dtype=object),
        }
        profile: dict[str, float | int] = {
            "corporate_action_index_seconds": 0.0,
            "corporate_action_select_seconds": 0.0,
            "corporate_action_gather_seconds": 0.0,
            "corporate_action_rows": int(batch),
            "corporate_action_max_items": int(max_items),
        }
        if max_items <= 0 or batch <= 0:
            return out, profile
        origin_timestamps = _identity_arrays(parts, refs)[2]
        for part_index, rows in _rows_by_part(refs).items():
            part = parts[int(part_index)]
            frame = part.context.get("corporate_actions")
            if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                continue
            index_start = time.perf_counter()
            index = self._corporate_action_context_index(frame, max_items=max_items)
            profile["corporate_action_index_seconds"] = float(profile["corporate_action_index_seconds"]) + (time.perf_counter() - index_start)
            if index.item_count <= 0:
                continue
            select_start = time.perf_counter()
            selected_indices, selected_mask = _select_corporate_action_item_indices(index, origin_timestamps[rows], max_items=max_items)
            profile["corporate_action_select_seconds"] = float(profile["corporate_action_select_seconds"]) + (time.perf_counter() - select_start)
            if not bool(selected_mask.any()):
                continue
            gather_start = time.perf_counter()
            safe_indices = np.where(selected_mask, selected_indices, 0)
            out["mask"][rows] = selected_mask
            for key in ("action_type_id", "dividend_type_id", "currency_id", "frequency_id", "effective_epoch_day", "declaration_epoch_day", "pay_epoch_day", "record_epoch_day"):
                values = getattr(index, key)[safe_indices].astype(out[key].dtype, copy=False)
                values[~selected_mask] = 0
                out[key][rows] = values
            available_timestamps = index.available_timestamps_us[safe_indices]
            effective_timestamps = index.effective_timestamps_us[safe_indices]
            available_timestamps[~selected_mask] = 0
            effective_timestamps[~selected_mask] = 0
            out["available_timestamp_us"][rows] = available_timestamps
            out["effective_timestamp_us"][rows] = effective_timestamps
            numeric = index.numeric_features[safe_indices]
            numeric[~selected_mask] = 0.0
            out["numeric_features"][rows] = numeric
            origins = np.broadcast_to(origin_timestamps[rows, None], available_timestamps.shape)
            available_features = np.concatenate([index.available_time_features[safe_indices], _relative_time_feature_matrix(available_timestamps, origins)], axis=-1).astype(np.float32, copy=False)
            effective_features = np.concatenate([index.effective_time_features[safe_indices], _relative_time_feature_matrix(effective_timestamps, origins)], axis=-1).astype(np.float32, copy=False)
            available_features[~selected_mask] = 0.0
            effective_features[~selected_mask] = 0.0
            out["time_features"][rows] = available_features
            out["effective_time_features"][rows] = effective_features
            profile["corporate_action_gather_seconds"] = float(profile["corporate_action_gather_seconds"]) + (time.perf_counter() - gather_start)
        return out, profile

    def _corporate_action_context_index(self, frame: Any, *, max_items: int) -> CorporateActionContextIndex:
        key = (id(frame), int(max_items))
        with self._corporate_action_index_lock:
            cached = self._corporate_action_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_corporate_action_context_index(frame)
            self._corporate_action_index_cache[key] = index
            return index

    def _materialize_intraday_bar_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
        if "intraday_bars" not in self.config.data_groups:
            return {}, {}
        horizons = _cached_intraday_context_horizons(parts, config=self.config)
        horizon_count = len(horizons)
        batch = int(len(refs))
        key = BAR_INPUT_GROUP_TO_KEY["intraday_bars"]
        values = np.zeros((batch, horizon_count, len(BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((batch, horizon_count), dtype=np.bool_)
        time_features = np.zeros((batch, horizon_count, len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
        end_time_features = np.zeros((batch, horizon_count, len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
        family_values = {
            family: np.zeros((batch, horizon_count, len(BAR_SOURCE_FEATURE_KEYS[family])), dtype=np.float32)
            for family in BAR_FAMILY_KEYS
        }
        family_masks = {
            family: np.zeros((batch, horizon_count), dtype=np.bool_)
            for family in BAR_FAMILY_KEYS
        }
        family_time_features = {
            family: np.zeros((batch, horizon_count, len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
            for family in BAR_FAMILY_KEYS
        }
        family_end_time_features = {
            family: np.zeros((batch, horizon_count, len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
            for family in BAR_FAMILY_KEYS
        }
        profile: dict[str, float | int] = {
            "intraday_bar_lookup_seconds": 0.0,
            "intraday_bar_gather_seconds": 0.0,
            "intraday_bar_compact_seconds": 0.0,
        }
        if horizon_count <= 0 or batch <= 0:
            return {key: _intraday_bar_payload(values, mask, time_features, end_time_features, family_values, family_masks, family_time_features, family_end_time_features, horizons)}, profile
        origin_timestamps = _identity_arrays(parts, refs)[2]
        for part_index, rows in _rows_by_part(refs).items():
            part = parts[int(part_index)]
            frame = part.context.get("intraday_bars")
            if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                compact_frame = part.context.get("intraday_base_bars")
                if compact_frame is not None and int(getattr(compact_frame, "height", 0) or 0) > 0:
                    compact_start = time.perf_counter()
                    self._materialize_intraday_bar_inputs_from_compact(
                        part=part,
                        refs=refs,
                        rows=rows,
                        horizons=horizons,
                        values=values,
                        mask=mask,
                        time_features=time_features,
                        family_values=family_values,
                        family_masks=family_masks,
                        family_time_features=family_time_features,
                        end_time_features=end_time_features,
                        family_end_time_features=family_end_time_features,
                    )
                    profile["intraday_bar_compact_seconds"] = float(profile["intraday_bar_compact_seconds"]) + (time.perf_counter() - compact_start)
                    continue
                if self.config.strict_audit:
                    raise RuntimeError(f"Missing intraday context bars for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
                continue
            origin_rows = _origin_rows_for_refs(refs, rows)
            lookup_start = time.perf_counter()
            label_index = self._label_context_index_for_frame(part, frame, horizon_count)
            max_origin_row = int(origin_rows.max()) if int(origin_rows.shape[0]) else -1
            if label_index.row_count < max_origin_row + 1:
                raise RuntimeError(f"Origin/intraday-bar index length mismatch for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
            missing = label_index.missing_mask[origin_rows]
            found = ~missing
            profile["intraday_bar_lookup_seconds"] = float(profile["intraday_bar_lookup_seconds"]) + (time.perf_counter() - lookup_start)
            if self.config.strict_audit and not bool(found.all()):
                missing_pos = int(np.flatnonzero(~found)[0])
                origin = int(part.origin_array("origin_ordinal")[int(origin_rows[missing_pos])])
                raise RuntimeError(f"Missing intraday context bars for {part.plan.month}:{part.plan.ticker}|{origin}.")
            if not np.any(found):
                continue
            output_rows = rows[found]
            source_indices = label_index.label_rows[origin_rows[found]]
            gather_start = time.perf_counter()
            start_ts = _label_column_matrix_for_rows(frame, "context_grid_start_timestamp_us", source_indices, horizon_count, np.dtype(np.int64))
            if "context_grid_end_timestamp_us" in frame.columns:
                end_ts = _label_column_matrix_for_rows(frame, "context_grid_end_timestamp_us", source_indices, horizon_count, np.dtype(np.int64))
            else:
                specs = _intraday_horizon_specs(horizons)
                horizon_us = np.asarray([int(spec[1]) for spec in specs], dtype=np.int64)
                end_ts = np.minimum(start_ts + horizon_us[None, :], origin_timestamps[output_rows, None].astype(np.int64, copy=False))
            selected_mask = _label_column_matrix_for_rows(frame, "available", source_indices, horizon_count, np.dtype(np.bool_))
            gathered_time = _bar_time_feature_matrix(start_ts, origin_timestamps[output_rows, None])
            gathered_end_time = _bar_end_time_feature_matrix(end_ts, origin_timestamps[output_rows, None])
            gathered_time[~selected_mask] = 0.0
            gathered_end_time[~selected_mask] = 0.0
            time_features[output_rows] = gathered_time
            end_time_features[output_rows] = gathered_end_time
            mask[output_rows] = selected_mask
            for family in BAR_FAMILY_KEYS:
                fields = BAR_SOURCE_FEATURE_KEYS[family]
                for field_index, field_name in enumerate(fields):
                    family_values[family][output_rows, :, field_index] = _label_column_matrix_for_rows(
                        frame,
                        f"{family}_{field_name}",
                        source_indices,
                        horizon_count,
                        np.dtype(np.float32),
                    )
                family_mask = _label_column_matrix_for_rows(frame, f"{family}_available", source_indices, horizon_count, np.dtype(np.bool_))
                family_masks[family][output_rows] = family_mask
                family_time = gathered_time.copy()
                family_time[~family_mask] = 0.0
                family_time_features[family][output_rows] = family_time
                family_end_time = gathered_end_time.copy()
                family_end_time[~family_mask] = 0.0
                family_end_time_features[family][output_rows] = family_end_time
                if family == "trade":
                    trade_fields = list(fields)
                    for output_field, source_field in (("open", "open"), ("close", "close"), ("high", "high"), ("low", "low"), ("size_sum", "size_sum"), ("event_count", "event_count")):
                        if output_field in BAR_FEATURE_KEYS and source_field in trade_fields:
                            values[output_rows, :, BAR_FEATURE_KEYS.index(output_field)] = family_values[family][output_rows, :, trade_fields.index(source_field)]
            profile["intraday_bar_gather_seconds"] = float(profile["intraday_bar_gather_seconds"]) + (time.perf_counter() - gather_start)
        payload = _intraday_bar_payload(values, mask, time_features, end_time_features, family_values, family_masks, family_time_features, family_end_time_features, horizons)
        return {key: payload}, profile

    def _materialize_intraday_bar_inputs_from_compact(
        self,
        *,
        part: LoadedDailyIndexPart,
        refs: Sequence[DailyIndexSampleRef],
        rows: np.ndarray,
        horizons: Sequence[str],
        values: np.ndarray,
        mask: np.ndarray,
        time_features: np.ndarray,
        family_values: dict[str, np.ndarray],
        family_masks: dict[str, np.ndarray],
        family_time_features: dict[str, np.ndarray],
        end_time_features: np.ndarray,
        family_end_time_features: dict[str, np.ndarray],
    ) -> None:
        horizon_count = int(len(horizons))
        if horizon_count <= 0 or rows.shape[0] <= 0:
            return
        specs = _intraday_horizon_specs(horizons)
        if len(specs) != horizon_count:
            raise RuntimeError(f"Intraday context horizon count mismatch for {_part_key(part.plan)}.")
        origins = part.origins
        if origins is None:
            raise RuntimeError(f"Missing origins for {_part_key(part.plan)}.")
        missing_columns = {"origin_timestamp_us", "origin_local_date", "origin_local_session_us"}.difference(set(origins.columns))
        if missing_columns:
            raise RuntimeError(f"Compact intraday bar materialization requires origin columns {sorted(missing_columns)} for {_part_key(part.plan)}. Rebuild the cache.")
        batch = int(values.shape[0])
        labels_out = {key: np.zeros((batch, horizon_count), dtype=dtype) for key, dtype in LABEL_VALUE_DTYPES.items()}
        origin_rows = _origin_rows_for_refs(refs, rows)
        origin_timestamps = part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[origin_rows]
        local_dates = np.asarray(origins.get_column("origin_local_date").to_numpy(), dtype=object)[origin_rows]
        local_dates = np.asarray([str(value)[:10] for value in local_dates], dtype=object)
        local_session_us = part.origin_array("origin_local_session_us").astype(np.int64, copy=False)[origin_rows]
        local_midnight_us = origin_timestamps - local_session_us
        compact_index = self._intraday_compact_label_index(part)
        out_rows = rows.astype(np.int64, copy=False)
        for horizon_index, (_horizon, _horizon_us, resolution_us, bucket_count, is_eod) in enumerate(specs):
            origin_bucket = local_session_us // int(resolution_us)
            last_bucket = origin_bucket - 1
            if is_eod:
                first_bucket = np.zeros_like(last_bucket)
            else:
                first_bucket = np.maximum(0, last_bucket - int(bucket_count) + 1)
            valid = last_bucket >= first_bucket
            grid_start_session_us = first_bucket * int(resolution_us)
            grid_end_session_us = (last_bucket + 1) * int(resolution_us)
            grid_start_ts = local_midnight_us + grid_start_session_us
            grid_end_ts = local_midnight_us + grid_end_session_us
            labels_out["label_grid_start_timestamp_us"][out_rows, horizon_index] = grid_start_ts.astype(np.int64, copy=False)
            labels_out["label_grid_end_timestamp_us"][out_rows, horizon_index] = grid_end_ts.astype(np.int64, copy=False)
            for family in BAR_FAMILY_KEYS:
                self._fill_compact_bar_family(
                    compact_index,
                    family=family,
                    dates=local_dates,
                    first_bucket=first_bucket,
                    last_bucket=last_bucket,
                    valid=valid,
                    output_rows=out_rows,
                    horizon_index=horizon_index,
                    resolution_us=int(resolution_us),
                    labels_out=labels_out,
                )
            selected_mask = np.zeros((int(out_rows.shape[0]),), dtype=np.bool_)
            for family in BAR_FAMILY_KEYS:
                selected_mask |= labels_out[f"{family}_available"][out_rows, horizon_index].astype(np.bool_, copy=False)
            if not bool(selected_mask.any()):
                continue
            gathered_time = _bar_time_feature_matrix(grid_start_ts, origin_timestamps).astype(np.float32, copy=False)
            gathered_end_time = _bar_end_time_feature_matrix(grid_end_ts, origin_timestamps).astype(np.float32, copy=False)
            gathered_time[~selected_mask] = 0.0
            gathered_end_time[~selected_mask] = 0.0
            time_features[out_rows, horizon_index] = gathered_time
            end_time_features[out_rows, horizon_index] = gathered_end_time
            mask[out_rows, horizon_index] = selected_mask
            for family in BAR_FAMILY_KEYS:
                fields = BAR_SOURCE_FEATURE_KEYS[family]
                rows_family_mask = labels_out[f"{family}_available"][out_rows, horizon_index].astype(np.bool_, copy=False)
                family_masks[family][out_rows, horizon_index] = rows_family_mask
                family_time = gathered_time.copy()
                family_time[~rows_family_mask] = 0.0
                family_time_features[family][out_rows, horizon_index] = family_time
                family_end_time = gathered_end_time.copy()
                family_end_time[~rows_family_mask] = 0.0
                family_end_time_features[family][out_rows, horizon_index] = family_end_time
                for field_index, field_name in enumerate(fields):
                    source_key = f"{family}_{field_name}"
                    if source_key in labels_out:
                        family_values[family][out_rows, horizon_index, field_index] = labels_out[source_key][out_rows, horizon_index].astype(np.float32, copy=False)
            trade_fields = list(BAR_SOURCE_FEATURE_KEYS["trade"])
            for output_field, source_field in (("open", "open"), ("close", "close"), ("high", "high"), ("low", "low"), ("size_sum", "size_sum"), ("event_count", "event_count")):
                if output_field in BAR_FEATURE_KEYS and source_field in trade_fields:
                    values[out_rows, horizon_index, BAR_FEATURE_KEYS.index(output_field)] = family_values["trade"][out_rows, horizon_index, trade_fields.index(source_field)]

    def _label_context_index_for_frame(self, part: LoadedDailyIndexPart, frame: Any, horizon_count: int) -> LabelContextIndex:
        key = (id(part.origins), id(frame), int(horizon_count))
        with self._label_index_lock:
            cached = self._label_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_label_context_index(part.origins, frame, int(horizon_count), strict=bool(self.config.strict_audit), part_key=_part_key(part.plan))
            self._label_index_cache[key] = index
            return index

    def _materialize_corporate_action_labels(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, np.ndarray], tuple[int, ...], dict[str, float | int]]:
        if not CORPORATE_ACTION_LABEL_GROUPS.intersection(set(self.config.data_groups)):
            return {}, (), {}
        days = _cached_corporate_action_label_days(parts)
        if not days:
            days = tuple(int(day) for day in self.config.corporate_action_label_days)
        horizon_count = len(days)
        out = {key: np.zeros((len(refs), horizon_count), dtype=dtype) for key, dtype in CORPORATE_ACTION_LABEL_DTYPES.items()}
        profile: dict[str, float | int] = {
            "corporate_action_label_lookup_seconds": 0.0,
            "corporate_action_label_gather_seconds": 0.0,
            "corporate_action_label_derived_parts": 0,
            "corporate_action_label_dense_parts": 0,
            "corporate_action_label_missing_context_parts": 0,
        }
        if horizon_count <= 0:
            return out, days, profile
        origin_timestamps = _identity_arrays(parts, refs)[2]
        for part_index, rows in _rows_by_part(refs).items():
            part = parts[int(part_index)]
            labels = part.context.get("corporate_action_daily_labels")
            if labels is None or int(getattr(labels, "height", 0) or 0) <= 0:
                context = part.context.get("corporate_actions")
                if context is None:
                    profile["corporate_action_label_missing_context_parts"] = int(profile["corporate_action_label_missing_context_parts"]) + 1
                    if self.config.strict_audit:
                        raise RuntimeError(
                            "Missing corporate action labels and corporate action context for "
                            f"{part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}."
                        )
                    continue
                derive_start = time.perf_counter()
                self._derive_corporate_action_labels_from_context(
                    part=part,
                    context=context,
                    origin_timestamps_us=origin_timestamps[rows],
                    output_rows=rows,
                    days=days,
                    out=out,
                )
                profile["corporate_action_label_gather_seconds"] = float(profile["corporate_action_label_gather_seconds"]) + (time.perf_counter() - derive_start)
                profile["corporate_action_label_derived_parts"] = int(profile["corporate_action_label_derived_parts"]) + 1
                continue
            origin_rows = _origin_rows_for_refs(refs, rows)
            lookup_start = time.perf_counter()
            max_origin_row = int(origin_rows.max()) if int(origin_rows.shape[0]) else -1
            if int(labels.height) < max_origin_row + 1:
                raise RuntimeError(f"Origin/corporate-action-label row count mismatch for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
            source_indices = origin_rows.astype(np.int64, copy=False)
            profile["corporate_action_label_lookup_seconds"] = float(profile["corporate_action_label_lookup_seconds"]) + (time.perf_counter() - lookup_start)
            gather_start = time.perf_counter()
            for key, dtype in CORPORATE_ACTION_LABEL_DTYPES.items():
                out[key][rows] = _label_column_matrix_for_rows(labels, key, source_indices, horizon_count, dtype)
            profile["corporate_action_label_gather_seconds"] = float(profile["corporate_action_label_gather_seconds"]) + (time.perf_counter() - gather_start)
            profile["corporate_action_label_dense_parts"] = int(profile["corporate_action_label_dense_parts"]) + 1
        return out, days, profile

    def _derive_corporate_action_labels_from_context(
        self,
        *,
        part: LoadedDailyIndexPart,
        context: Any,
        origin_timestamps_us: np.ndarray,
        output_rows: np.ndarray,
        days: Sequence[int],
        out: dict[str, np.ndarray],
    ) -> None:
        if int(getattr(context, "height", 0) or 0) <= 0 or int(origin_timestamps_us.shape[0]) <= 0:
            return
        index = self._corporate_action_context_index(context, max_items=int(self.config.corporate_action_max_items))
        if index.item_count <= 0:
            return
        effective = np.asarray(index.effective_timestamps_us, dtype=np.int64)
        valid_effective = effective > 0
        if not bool(valid_effective.any()):
            return
        origins = np.asarray(origin_timestamps_us, dtype=np.int64)
        horizon_us = np.asarray(days, dtype=np.int64) * np.int64(86_400_000_000)
        future = (
            (effective[None, None, :] > origins[:, None, None])
            & (effective[None, None, :] <= (origins[:, None, None] + horizon_us[None, :, None]))
            & valid_effective[None, None, :]
        )
        if not bool(future.any()):
            return
        numeric = index.numeric_features
        if numeric.shape[1] < len(CORPORATE_ACTION_NUMERIC_FEATURE_KEYS):
            raise RuntimeError(f"Corporate action context is missing numeric features for {_part_key(part.plan)}.")
        flag_by_label = {
            "future_split_flag": numeric[:, CORPORATE_ACTION_NUMERIC_FEATURE_KEYS.index("is_split")] > 0.5,
            "future_reverse_split_flag": numeric[:, CORPORATE_ACTION_NUMERIC_FEATURE_KEYS.index("is_reverse_split")] > 0.5,
            "future_forward_split_flag": numeric[:, CORPORATE_ACTION_NUMERIC_FEATURE_KEYS.index("is_forward_split")] > 0.5,
            "future_dividend_ex_flag": numeric[:, CORPORATE_ACTION_NUMERIC_FEATURE_KEYS.index("is_dividend")] > 0.5,
            "future_special_dividend_ex_flag": numeric[:, CORPORATE_ACTION_NUMERIC_FEATURE_KEYS.index("is_special_dividend")] > 0.5,
        }
        for key, flags in flag_by_label.items():
            out[key][output_rows] = (future & flags[None, None, :]).any(axis=2)
        out["future_any_corporate_action_flag"][output_rows] = future.any(axis=2)

    def _materialize_bar_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
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
                end_time_features = np.zeros((len(refs), int(offsets.shape[0]), len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
                family_values = {
                    family: np.zeros((len(refs), int(offsets.shape[0]), len(BAR_SOURCE_FEATURE_KEYS[family])), dtype=np.float32)
                    for family in BAR_FAMILY_KEYS
                }
                family_masks = {
                    family: np.zeros((len(refs), int(offsets.shape[0])), dtype=np.bool_)
                    for family in BAR_FAMILY_KEYS
                }
                family_time_features = {
                    family: np.zeros((len(refs), int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
                    for family in BAR_FAMILY_KEYS
                }
                family_end_time_features = {
                    family: np.zeros((len(refs), int(offsets.shape[0]), len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
                    for family in BAR_FAMILY_KEYS
                }
                for part_index, rows in grouped_rows.items():
                    part = parts[int(part_index)]
                    frame = part.context.get(group)
                    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
                        continue
                    index_start = time.perf_counter()
                    index = self._daily_bar_context_index(frame)
                    profile["bar_index_seconds"] = float(profile["bar_index_seconds"]) + (time.perf_counter() - index_start)
                    symbol = str(part.plan.ticker)
                    for family in BAR_FAMILY_KEYS:
                        starts_by_symbol = index.bar_start_ms_by_family_symbol.get(family, {})
                        if symbol not in starts_by_symbol:
                            continue
                        select_start = time.perf_counter()
                        selected, selected_mask = _select_completed_bar_rows(starts_by_symbol[symbol], cutoff_ms[rows], offsets)
                        profile["bar_select_seconds"] = float(profile["bar_select_seconds"]) + (time.perf_counter() - select_start)
                        if not bool(selected_mask.any()):
                            continue
                        gather_start = time.perf_counter()
                        safe_selected = np.where(selected_mask, selected, 0)
                        gathered = index.values_by_family_symbol[family][symbol][safe_selected]
                        gathered[~selected_mask] = 0.0
                        selected_starts = starts_by_symbol[symbol][safe_selected]
                        gathered_time = _bar_time_feature_matrix(selected_starts.astype(np.int64, copy=False) * 1000, origin_timestamps_ms[rows, None].astype(np.int64, copy=False) * 1000)
                        selected_ends = selected_starts + 86_400_000
                        gathered_end_time = _bar_end_time_feature_matrix(selected_ends.astype(np.int64, copy=False) * 1000, origin_timestamps_ms[rows, None].astype(np.int64, copy=False) * 1000)
                        gathered_time[~selected_mask] = 0.0
                        gathered_end_time[~selected_mask] = 0.0
                        family_values[family][rows] = gathered[:, :, BAR_SOURCE_FEATURE_COLUMNS[family]]
                        family_masks[family][rows] = selected_mask
                        family_time_features[family][rows] = gathered_time
                        family_end_time_features[family][rows] = gathered_end_time
                        if family == "trade":
                            values[rows] = gathered
                            mask[rows] = selected_mask
                            time_features[rows] = gathered_time
                            end_time_features[rows] = gathered_end_time
                        profile["bar_gather_seconds"] = float(profile["bar_gather_seconds"]) + (time.perf_counter() - gather_start)
                payload = {
                    "values": values,
                    "mask": mask,
                    "time_features": time_features,
                    "start_time_features": time_features,
                    "end_time_features": end_time_features,
                    "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                    "start_time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                    "end_time_feature_names": np.asarray(BAR_END_FEATURE_COLUMNS, dtype=object),
                    "offsets": offsets.astype(np.int32, copy=False),
                    "feature_names": np.asarray(BAR_FEATURE_KEYS, dtype=object),
                }
                for family in BAR_FAMILY_KEYS:
                    payload[f"{family}_values"] = family_values[family]
                    payload[f"{family}_mask"] = family_masks[family]
                    payload[f"{family}_time_features"] = family_time_features[family]
                    payload[f"{family}_start_time_features"] = family_time_features[family]
                    payload[f"{family}_end_time_features"] = family_end_time_features[family]
                    payload[f"{family}_feature_names"] = np.asarray(BAR_SOURCE_FEATURE_KEYS[family], dtype=object)
                out[key] = payload
                continue
            symbols: tuple[str, ...] = ()
            values = np.zeros((len(refs), 0, int(offsets.shape[0]), len(BAR_FEATURE_KEYS)), dtype=np.float32)
            mask = np.zeros((len(refs), 0, int(offsets.shape[0])), dtype=np.bool_)
            time_features = np.zeros((len(refs), 0, int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
            end_time_features = np.zeros((len(refs), 0, int(offsets.shape[0]), len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
            family_values: dict[str, np.ndarray] = {}
            family_masks: dict[str, np.ndarray] = {}
            family_time_features: dict[str, np.ndarray] = {}
            family_end_time_features: dict[str, np.ndarray] = {}
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
                    end_time_features = np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
                    family_values = {
                        family: np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_SOURCE_FEATURE_KEYS[family])), dtype=np.float32)
                        for family in BAR_FAMILY_KEYS
                    }
                    family_masks = {
                        family: np.zeros((len(refs), len(symbols), int(offsets.shape[0])), dtype=np.bool_)
                        for family in BAR_FAMILY_KEYS
                    }
                    family_time_features = {
                        family: np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_TIME_FEATURE_COLUMNS)), dtype=np.float32)
                        for family in BAR_FAMILY_KEYS
                    }
                    family_end_time_features = {
                        family: np.zeros((len(refs), len(symbols), int(offsets.shape[0]), len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32)
                        for family in BAR_FAMILY_KEYS
                    }
                    symbol_array = np.asarray(symbols, dtype=object)
                gather_start = time.perf_counter()
                for symbol_index, symbol in enumerate(symbols):
                    for family in BAR_FAMILY_KEYS:
                        starts_by_symbol = index.bar_start_ms_by_family_symbol.get(family, {})
                        if symbol not in starts_by_symbol:
                            continue
                        selected, selected_mask = _select_completed_bar_rows(starts_by_symbol[symbol], cutoff_ms[rows], offsets)
                        if not bool(selected_mask.any()):
                            continue
                        safe_selected = np.where(selected_mask, selected, 0)
                        gathered = index.values_by_family_symbol[family][symbol][safe_selected]
                        gathered[~selected_mask] = 0.0
                        selected_starts = starts_by_symbol[symbol][safe_selected]
                        gathered_time = _bar_time_feature_matrix(selected_starts.astype(np.int64, copy=False) * 1000, origin_timestamps_ms[rows, None].astype(np.int64, copy=False) * 1000)
                        selected_ends = selected_starts + 86_400_000
                        gathered_end_time = _bar_end_time_feature_matrix(selected_ends.astype(np.int64, copy=False) * 1000, origin_timestamps_ms[rows, None].astype(np.int64, copy=False) * 1000)
                        gathered_time[~selected_mask] = 0.0
                        gathered_end_time[~selected_mask] = 0.0
                        family_values[family][rows, symbol_index] = gathered[:, :, BAR_SOURCE_FEATURE_COLUMNS[family]]
                        family_masks[family][rows, symbol_index] = selected_mask
                        family_time_features[family][rows, symbol_index] = gathered_time
                        family_end_time_features[family][rows, symbol_index] = gathered_end_time
                        if family == "trade":
                            values[rows, symbol_index] = gathered
                            mask[rows, symbol_index] = selected_mask
                            time_features[rows, symbol_index] = gathered_time
                            end_time_features[rows, symbol_index] = gathered_end_time
                profile["bar_gather_seconds"] = float(profile["bar_gather_seconds"]) + (time.perf_counter() - gather_start)
            payload = {
                "values": values,
                "mask": mask,
                "time_features": time_features,
                "start_time_features": time_features,
                "end_time_features": end_time_features,
                "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                "start_time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
                "end_time_feature_names": np.asarray(BAR_END_FEATURE_COLUMNS, dtype=object),
                "offsets": offsets.astype(np.int32, copy=False),
                "symbols": symbol_array,
                "feature_names": np.asarray(BAR_FEATURE_KEYS, dtype=object),
            }
            for family in BAR_FAMILY_KEYS:
                empty_family_values = np.zeros(
                    (len(refs), len(symbols), int(offsets.shape[0]), len(BAR_SOURCE_FEATURE_KEYS[family])),
                    dtype=np.float32,
                )
                payload[f"{family}_values"] = family_values.get(family, empty_family_values)
                payload[f"{family}_mask"] = family_masks.get(family, np.zeros_like(mask))
                payload[f"{family}_time_features"] = family_time_features.get(family, np.zeros_like(time_features))
                payload[f"{family}_start_time_features"] = family_time_features.get(family, np.zeros_like(time_features))
                payload[f"{family}_end_time_features"] = family_end_time_features.get(family, np.zeros_like(end_time_features))
                payload[f"{family}_feature_names"] = np.asarray(BAR_SOURCE_FEATURE_KEYS[family], dtype=object)
            out[key] = payload
        return out, profile

    def _materialize_scanner_inputs(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        payload = _empty_scanner_payload(len(refs), self.config)
        if "scanner_context" not in self.config.data_groups or not refs:
            return payload, {"scanner_zero_fallback": int(1)}
        indexes_by_part: dict[int, ScannerArtifactIndex] = {}
        rows_by_part = _rows_by_part(refs)
        artifact_rows = 0
        missing_dates: set[str] = set()
        index_start = time.perf_counter()
        for part_index, part in enumerate(parts):
            candidate = part.context.get("scanner_context")
            if candidate is None or int(getattr(candidate, "height", 0) or 0) <= 0:
                if int(part_index) in rows_by_part:
                    missing_dates.add(str(part.plan.source_date))
                continue
            path = part.context_paths.get("scanner_context")
            index = self._scanner_artifact_index(candidate, cache_key=str(path.resolve()) if path else f"frame:{id(candidate)}")
            indexes_by_part[id(part)] = index
            artifact_rows = max(artifact_rows, int(index.bucket.shape[0]))
        index_seconds = time.perf_counter() - index_start
        if not indexes_by_part:
            if self.config.scanner_required:
                dates = sorted({str(part.plan.source_date) for part in parts})
                raise RuntimeError(f"scanner_required=True but no scanner artifact was loaded for dates {dates}.")
            return payload, {"scanner_zero_fallback": int(1), "scanner_artifact_rows": int(0)}
        profile: dict[str, float | int] = {
            "scanner_index_seconds": float(index_seconds),
            "scanner_artifact_rows": int(artifact_rows),
            "scanner_zero_fallback": int(0),
            "scanner_index_cache_entries": int(len(indexes_by_part)),
            "scanner_missing_artifact_dates": int(len(missing_dates)),
        }
        if artifact_rows <= 0:
            return payload, profile
        gather_start = time.perf_counter()
        group_names = tuple(str(group) for group in self.config.scanner_groups)
        horizons = tuple(str(horizon) for horizon in self.config.scanner_horizons)
        top_k = max(1, int(self.config.scanner_top_k))
        origin_timestamps = _identity_arrays(parts, refs)[2]
        available = 0
        for output_row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            index = indexes_by_part.get(id(part))
            if index is None or index.bucket.shape[0] <= 0:
                continue
            origin_row = int(ref.origin_row)
            origin_date = str(part.origins.get_column("origin_local_date")[origin_row])[:10] if part.origins is not None and "origin_local_date" in part.origins.columns else str(part.plan.source_date)[:10]
            origin_session_us = int(part.origin_array("origin_local_session_us")[origin_row]) if part.origins is not None and "origin_local_session_us" in part.origins.columns else 0
            scanner_resolution_us = int(index.columns.get("scanner_resolution_us", np.asarray([1_000_000], dtype=np.int64))[0])
            bucket = (max(0, origin_session_us - 1) // max(1, scanner_resolution_us))
            origin_ticker = str(part.plan.ticker)
            origin_key = (origin_date, int(bucket), origin_ticker)
            origin_artifact_row = index.row_by_key.get(origin_key)
            for group_index, group_name in enumerate(group_names):
                leader_rows = index.leaders_by_key.get((origin_date, int(bucket), group_name))
                if leader_rows is None or leader_rows.size == 0:
                    continue
                for leader_position, artifact_row in enumerate(leader_rows[:top_k]):
                    artifact_row = int(artifact_row)
                    payload["leader_ticker_id"][output_row, group_index, leader_position] = int(index.ticker_id[artifact_row])
                    payload["leader_rank"][output_row, group_index, leader_position] = int(index.columns[f"{group_name}_rank"][artifact_row])
                    payload["leader_mask"][output_row, group_index, leader_position] = True
                    payload["leader_horizon_mask"][output_row, group_index, leader_position] = _fill_scanner_values_from_artifact_row(
                        payload["leader_values"][output_row, group_index, leader_position],
                        payload["leader_time_features"][output_row, group_index, leader_position],
                        payload["leader_start_time_features"][output_row, group_index, leader_position],
                        payload["leader_end_time_features"][output_row, group_index, leader_position],
                        index=index,
                        artifact_row=artifact_row,
                        horizons=horizons,
                        origin_timestamp_us=int(origin_timestamps[output_row]),
                    )
                if origin_artifact_row is not None:
                    origin_artifact_row = int(origin_artifact_row)
                    payload["origin_mask"][output_row, group_index] = True
                    rank = int(index.columns.get(f"{group_name}_rank", np.full(index.bucket.shape, -1, dtype=np.int32))[origin_artifact_row])
                    score = float(index.columns.get(f"{group_name}_score", np.zeros(index.bucket.shape, dtype=np.float32))[origin_artifact_row])
                    percentile = float(index.columns.get(f"{group_name}_percentile", np.zeros(index.bucket.shape, dtype=np.float32))[origin_artifact_row])
                    payload["origin_rank"][output_row, group_index] = rank
                    payload["origin_in_topk"][output_row, group_index] = bool(0 <= rank < top_k)
                    payload["origin_topk_position"][output_row, group_index] = rank if 0 <= rank < top_k else -1
                    payload["numeric_features"][output_row, group_index] = np.asarray(
                        [score, percentile, float(0 <= rank < top_k), float(rank if rank >= 0 else -1), float(rank), percentile],
                        dtype=np.float32,
                    )
                    payload["origin_horizon_mask"][output_row, group_index] = _fill_scanner_values_from_artifact_row(
                        payload["origin_values"][output_row, group_index],
                        payload["origin_time_features"][output_row, group_index],
                        payload["origin_start_time_features"][output_row, group_index],
                        payload["origin_end_time_features"][output_row, group_index],
                        index=index,
                        artifact_row=origin_artifact_row,
                        horizons=horizons,
                        origin_timestamp_us=int(origin_timestamps[output_row]),
                    )
                available += 1
        profile["scanner_gather_seconds"] = time.perf_counter() - gather_start
        profile["scanner_available_samples"] = int(payload["leader_mask"].reshape((len(refs), -1)).any(axis=1).sum()) if refs else 0
        profile["scanner_group_hits"] = int(available)
        return payload, profile

    def _scanner_artifact_index(self, frame: Any, *, cache_key: str | None = None) -> ScannerArtifactIndex:
        key = str(cache_key or f"frame:{id(frame)}")
        cache_limit = max(0, int(self.config.scanner_index_cache_entries))
        with self._scanner_artifact_index_lock:
            cached = self._scanner_artifact_index_cache.get(key)
            if cached is not None:
                self._scanner_artifact_index_cache.move_to_end(key)
                return cached
        if cache_key and cache_limit > 0:
            with self._shared_scanner_artifact_index_lock:
                cached = self._shared_scanner_artifact_index_cache.get(key)
                if cached is not None:
                    self._shared_scanner_artifact_index_cache.move_to_end(key)
                    self._remember_scanner_artifact_index(key, cached, cache_limit)
                    return cached
            index = self._build_scanner_artifact_index(frame)
            with self._shared_scanner_artifact_index_lock:
                cached = self._shared_scanner_artifact_index_cache.get(key)
                if cached is not None:
                    self._shared_scanner_artifact_index_cache.move_to_end(key)
                    self._remember_scanner_artifact_index(key, cached, cache_limit)
                    return cached
                self._shared_scanner_artifact_index_cache[key] = index
                self._shared_scanner_artifact_index_cache.move_to_end(key)
                while len(self._shared_scanner_artifact_index_cache) > cache_limit:
                    self._shared_scanner_artifact_index_cache.popitem(last=False)
                self._remember_scanner_artifact_index(key, index, cache_limit)
                return index
        index = self._build_scanner_artifact_index(frame)
        self._remember_scanner_artifact_index(key, index, cache_limit)
        return index

    def _remember_scanner_artifact_index(self, key: str, index: ScannerArtifactIndex, cache_limit: int) -> None:
        if cache_limit <= 0:
            return
        with self._scanner_artifact_index_lock:
            self._scanner_artifact_index_cache[key] = index
            self._scanner_artifact_index_cache.move_to_end(key)
            while len(self._scanner_artifact_index_cache) > cache_limit:
                self._scanner_artifact_index_cache.popitem(last=False)

    def _build_scanner_artifact_index(self, frame: Any) -> ScannerArtifactIndex:
        pl = _polars()
        if int(getattr(frame, "height", 0) or 0) <= 0:
            return ScannerArtifactIndex(
                source_date=np.asarray([], dtype=object),
                bucket=np.zeros((0,), dtype=np.int64),
                ticker=np.asarray([], dtype=object),
                ticker_id=np.zeros((0,), dtype=np.int64),
                timestamp_us=np.zeros((0,), dtype=np.int64),
                row_by_key={},
                leaders_by_key={},
                columns={},
            )
        source_date = np.asarray(frame.get_column("source_date").to_numpy(), dtype=object)
        source_date = np.asarray([str(value)[:10] for value in source_date], dtype=object)
        bucket = frame.get_column("scanner_bucket").to_numpy().astype(np.int64, copy=False)
        ticker = np.asarray(frame.get_column("ticker").to_numpy(), dtype=object)
        ticker_id = frame.get_column("ticker_id").to_numpy().astype(np.int64, copy=False) if "ticker_id" in frame.columns else np.zeros((int(frame.height),), dtype=np.int64)
        timestamp_us = frame.get_column("scanner_timestamp_us").to_numpy().astype(np.int64, copy=False)
        row_by_key = {(str(source_date[i]), int(bucket[i]), str(ticker[i])): int(i) for i in range(int(frame.height))}
        columns = {
            name: frame.get_column(name).to_numpy()
            for name in frame.columns
            if name not in {"source_date", "ticker", "ticker_id"}
        }
        leaders_by_key: dict[tuple[str, int, str], np.ndarray] = {}
        for group_name in self.config.scanner_groups:
            rank_col = f"{group_name}_rank"
            if rank_col not in frame.columns:
                continue
            ranked = (
                frame.with_row_index("_artifact_row")
                .filter((pl.col(rank_col) >= 0) & (pl.col(rank_col) < int(self.config.scanner_top_k)))
                .sort(["source_date", "scanner_bucket", rank_col])
            )
            for part_key, group in ranked.partition_by(["source_date", "scanner_bucket"], as_dict=True, maintain_order=True).items():
                date_value, bucket_value = part_key
                leaders_by_key[(str(date_value)[:10], int(bucket_value), str(group_name))] = group.get_column("_artifact_row").to_numpy().astype(np.int64, copy=False)
        return ScannerArtifactIndex(
            source_date=source_date,
            bucket=bucket,
            ticker=ticker,
            ticker_id=ticker_id,
            timestamp_us=timestamp_us,
            row_by_key=row_by_key,
            leaders_by_key=leaders_by_key,
            columns=columns,
        )

    def _daily_bar_context_index(self, frame: Any) -> DailyBarContextIndex:
        key = id(frame)
        with self._bar_index_lock:
            cached = self._bar_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_daily_bar_context_index(frame)
            self._bar_index_cache[key] = index
            return index

    def _materialize_encoded(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[np.ndarray, np.ndarray]:
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
                windows[flat]["event_meta"] = part.event_array("event_meta")[idx].astype(np.uint8, copy=False)
                windows[flat]["sip_timestamp_us"] = part.event_array("timestamp_us")[idx].astype(np.uint64, copy=False)
                windows[flat]["price_primary_int"] = part.event_array("price_primary_int")[idx].astype(np.uint32, copy=False)
                windows[flat]["price_secondary_int"] = part.event_array("price_secondary_int")[idx].astype(np.uint32, copy=False)
                windows[flat]["size_primary"] = part.event_array("size_primary")[idx].astype(np.float32, copy=False)
                windows[flat]["size_secondary"] = part.event_array("size_secondary")[idx].astype(np.float32, copy=False)
                windows[flat]["exchange_primary"] = part.event_array("exchange_primary")[idx].astype(np.uint8, copy=False)
                windows[flat]["exchange_secondary"] = part.event_array("exchange_secondary")[idx].astype(np.uint8, copy=False)
                windows[flat]["condition_token_1"] = part.event_array("condition_token_1")[idx].astype(np.uint8, copy=False)
                windows[flat]["condition_token_2"] = part.event_array("condition_token_2")[idx].astype(np.uint8, copy=False)
                windows[flat]["condition_token_3"] = part.event_array("condition_token_3")[idx].astype(np.uint8, copy=False)
                windows[flat]["condition_token_4"] = part.event_array("condition_token_4")[idx].astype(np.uint8, copy=False)
                windows[flat]["condition_token_5"] = part.event_array("condition_token_5")[idx].astype(np.uint8, copy=False)
                if start > 0:
                    previous[flat] = int(part.event_array("timestamp_us")[start - 1])
                flat += 1
        headers, events, valid, reasons = encode_unified_event_windows(windows, previous_sip_us=previous)
        if not bool(valid.all()):
            bad = int(np.flatnonzero(~valid)[0])
            raise RuntimeError(f"Encoded event window failed validation at flat window {bad}: {reasons[bad]!r}")
        return headers.reshape(len(refs), len(self.context_lags), HEADER_BYTES), events.reshape(len(refs), len(self.context_lags), 128, EVENT_BYTES)

    def _window_starts(self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> np.ndarray:
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
        self, parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray, np.ndarray, tuple[str, ...], dict[str, float | int]]:
        if "intraday_labels" not in self.config.data_groups and "labels" not in self.config.data_groups:
            return {}, {}, {}, {}, np.zeros((len(refs), 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32), np.zeros((len(refs), 0), dtype=np.bool_), (), {}
        horizons = _cached_horizons(parts, config=self.config)
        horizon_count = len(horizons)
        labels_out = {key: np.zeros((len(refs), horizon_count), dtype=dtype) for key, dtype in LABEL_VALUE_DTYPES.items()}
        family_values = {
            family: np.zeros((len(refs), horizon_count, len(fields)), dtype=np.float32)
            for family, fields in BAR_SOURCE_FEATURE_KEYS.items()
        }
        family_masks = {
            family: np.zeros((len(refs), horizon_count), dtype=np.bool_)
            for family in BAR_SOURCE_FEATURE_KEYS
        }
        family_feature_names = {
            family: np.asarray(fields, dtype=object)
            for family, fields in BAR_SOURCE_FEATURE_KEYS.items()
        }
        legacy_bars = np.zeros((len(refs), 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
        legacy_mask = np.zeros((len(refs), 0), dtype=np.bool_)
        profile: dict[str, float | int] = {
            "label_index_seconds": 0.0,
            "label_lookup_seconds": 0.0,
            "label_gather_seconds": 0.0,
        }
        for part_index, rows in _rows_by_part(refs).items():
            part = parts[int(part_index)]
            if part.labels is None or part.labels.height == 0:
                compact_start = time.perf_counter()
                self._materialize_intraday_labels_from_compact(part, refs, rows, horizon_count, labels_out)
                profile["label_compact_seconds"] = float(profile.get("label_compact_seconds", 0.0)) + (time.perf_counter() - compact_start)
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
        for family, fields in BAR_SOURCE_FEATURE_KEYS.items():
            values = family_values[family]
            for field_index, field_name in enumerate(fields):
                key = f"{family}_{field_name}"
                if key in labels_out:
                    values[:, :, field_index] = labels_out[key].astype(np.float32, copy=False)
            available_key = f"{family}_available"
            if available_key in labels_out:
                family_masks[family][:, :] = labels_out[available_key].astype(bool, copy=False)

        return labels_out, family_values, family_masks, family_feature_names, legacy_bars, legacy_mask, horizons, profile

    def _materialize_intraday_labels_from_compact(
        self,
        part: LoadedDailyIndexPart,
        refs: Sequence[DailyIndexSampleRef],
        rows: np.ndarray,
        horizon_count: int,
        labels_out: dict[str, np.ndarray],
    ) -> None:
        if horizon_count <= 0 or rows.shape[0] <= 0:
            return
        if "intraday_base_bars" not in part.context:
            if self.config.strict_audit:
                raise RuntimeError(f"Missing intraday_base_bars compact label source for {_part_key(part.plan)}.")
            return
        specs = _intraday_horizon_specs(_cached_horizons([part], config=self.config))
        if len(specs) != int(horizon_count):
            raise RuntimeError(f"Intraday horizon count mismatch for {_part_key(part.plan)}.")
        origin_rows = _origin_rows_for_refs(refs, rows)
        origins = part.origins
        if origins is None:
            raise RuntimeError(f"Missing origins for {_part_key(part.plan)}.")
        missing_columns = {"origin_timestamp_us", "origin_local_date", "origin_local_session_us"}.difference(set(origins.columns))
        if missing_columns:
            raise RuntimeError(f"Compact intraday label materialization requires origin columns {sorted(missing_columns)} for {_part_key(part.plan)}. Rebuild the cache.")
        origin_timestamps = part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[origin_rows]
        local_dates = np.asarray(origins.get_column("origin_local_date").to_numpy(), dtype=object)[origin_rows]
        local_dates = np.asarray([str(value)[:10] for value in local_dates], dtype=object)
        local_session_us = part.origin_array("origin_local_session_us").astype(np.int64, copy=False)[origin_rows]
        local_midnight_us = origin_timestamps - local_session_us
        compact_index = self._intraday_compact_label_index(part)
        out_rows = rows.astype(np.int64, copy=False)
        for horizon_index, (horizon, horizon_us, resolution_us, bucket_count, is_eod) in enumerate(specs):
            origin_bucket = local_session_us // int(resolution_us)
            first_bucket = origin_bucket + 1
            if is_eod:
                last_bucket = np.full_like(first_bucket, (SESSION_END_US - 1) // int(resolution_us))
            else:
                last_bucket = origin_bucket + int(bucket_count)
            grid_start_session_us = first_bucket * int(resolution_us)
            grid_end_session_us = (last_bucket + 1) * int(resolution_us)
            valid = grid_end_session_us <= SESSION_END_US
            grid_start_ts = local_midnight_us + grid_start_session_us
            grid_end_ts = local_midnight_us + grid_end_session_us
            labels_out["label_resolution_us"][out_rows, horizon_index] = np.uint64(resolution_us)
            labels_out["label_grid_start_timestamp_us"][out_rows, horizon_index] = grid_start_ts.astype(np.int64, copy=False)
            labels_out["label_grid_end_timestamp_us"][out_rows, horizon_index] = grid_end_ts.astype(np.int64, copy=False)
            labels_out["available"][out_rows, horizon_index] = False
            for family in BAR_FAMILY_KEYS:
                self._fill_compact_bar_family(
                    compact_index,
                    family=family,
                    dates=local_dates,
                    first_bucket=first_bucket,
                    last_bucket=last_bucket,
                    valid=valid,
                    output_rows=out_rows,
                    horizon_index=horizon_index,
                    resolution_us=int(resolution_us),
                    labels_out=labels_out,
                )
            labels_out["size_primary_sum"][out_rows, horizon_index] = (
                labels_out["quote_ask_size_open"][out_rows, horizon_index] + labels_out["quote_ask_size_close"][out_rows, horizon_index]
            ).astype(np.float32, copy=False)
            labels_out["size_secondary_sum"][out_rows, horizon_index] = (
                labels_out["quote_bid_size_open"][out_rows, horizon_index] + labels_out["quote_bid_size_close"][out_rows, horizon_index]
            ).astype(np.float32, copy=False)
            event_count = np.maximum.reduce(
                [
                    labels_out["trade_event_count"][out_rows, horizon_index].astype(np.uint64, copy=False),
                    labels_out["quote_bid_event_count"][out_rows, horizon_index].astype(np.uint64, copy=False),
                    labels_out["quote_ask_event_count"][out_rows, horizon_index].astype(np.uint64, copy=False),
                ]
            )
            labels_out["event_count"][out_rows, horizon_index] = event_count
            last_ts = np.maximum.reduce(
                [
                    labels_out["trade_last_event_timestamp_us"][out_rows, horizon_index].astype(np.int64, copy=False),
                    labels_out["quote_bid_last_event_timestamp_us"][out_rows, horizon_index].astype(np.int64, copy=False),
                    labels_out["quote_ask_last_event_timestamp_us"][out_rows, horizon_index].astype(np.int64, copy=False),
                ]
            )
            labels_out["last_event_timestamp_us"][out_rows, horizon_index] = last_ts
            labels_out["available"][out_rows, horizon_index] = valid & (event_count > 0)
            self._fill_compact_event_flags(
                compact_index,
                dates=local_dates,
                grid_start_ts=grid_start_ts,
                grid_end_ts=grid_end_ts,
                valid=valid,
                output_rows=out_rows,
                horizon_index=horizon_index,
                labels_out=labels_out,
            )

    def _fill_compact_bar_family(
        self,
        index: IntradayCompactLabelIndex,
        *,
        family: str,
        dates: np.ndarray,
        first_bucket: np.ndarray,
        last_bucket: np.ndarray,
        valid: np.ndarray,
        output_rows: np.ndarray,
        horizon_index: int,
        resolution_us: int,
        labels_out: dict[str, np.ndarray],
    ) -> None:
        for date_value in sorted({str(value) for value in dates}):
            date_mask = (dates == date_value) & valid
            if not bool(date_mask.any()):
                continue
            bars = index.bars.get((date_value, int(resolution_us), str(family)))
            if bars is None or bars.buckets.size == 0:
                continue
            local_positions = np.flatnonzero(date_mask)
            left = np.searchsorted(bars.buckets, first_bucket[local_positions], side="left")
            right = np.searchsorted(bars.buckets, last_bucket[local_positions], side="right")
            has = right > left
            if not bool(has.any()):
                continue
            positions = local_positions[has]
            left = left[has]
            right = right[has]
            rows = output_rows[positions]
            labels_out[f"{family}_open"][rows, horizon_index] = bars.open[left].astype(np.float32, copy=False)
            labels_out[f"{family}_close"][rows, horizon_index] = bars.close[right - 1].astype(np.float32, copy=False)
            labels_out[f"{family}_last_event_timestamp_us"][rows, horizon_index] = bars.last_event_timestamp_us[right - 1].astype(np.int64, copy=False)
            labels_out[f"{family}_event_count"][rows, horizon_index] = (bars.cum_event_count[right] - bars.cum_event_count[left]).astype(np.uint64, copy=False)
            if family == "trade":
                labels_out["trade_size_sum"][rows, horizon_index] = (bars.cum_size_sum[right] - bars.cum_size_sum[left]).astype(np.float32, copy=False)
            else:
                labels_out[f"{family}_size_open"][rows, horizon_index] = bars.size_open[left].astype(np.float32, copy=False)
                labels_out[f"{family}_size_close"][rows, horizon_index] = bars.size_close[right - 1].astype(np.float32, copy=False)
            for out_pos, lval, rval in zip(rows, left, right):
                sl = slice(int(lval), int(rval))
                labels_out[f"{family}_high"][int(out_pos), horizon_index] = np.float32(np.max(bars.high[sl]))
                labels_out[f"{family}_low"][int(out_pos), horizon_index] = np.float32(np.min(bars.low[sl]))
                if family != "trade":
                    labels_out[f"{family}_size_high"][int(out_pos), horizon_index] = np.float32(np.max(bars.size_high[sl]))
                    labels_out[f"{family}_size_low"][int(out_pos), horizon_index] = np.float32(np.min(bars.size_low[sl]))
            labels_out[f"{family}_available"][rows, horizon_index] = True

    def _fill_compact_event_flags(
        self,
        index: IntradayCompactLabelIndex,
        *,
        dates: np.ndarray,
        grid_start_ts: np.ndarray,
        grid_end_ts: np.ndarray,
        valid: np.ndarray,
        output_rows: np.ndarray,
        horizon_index: int,
        labels_out: dict[str, np.ndarray],
    ) -> None:
        for date_value in sorted({str(value) for value in dates}):
            date_mask = (dates == date_value) & valid
            if not bool(date_mask.any()):
                continue
            positions = np.flatnonzero(date_mask)
            rows = output_rows[positions]
            for key in ("ticker_news_arrival_flag", "sec_filing_arrival_flag"):
                source = index.ticker_news_by_date if key == "ticker_news_arrival_flag" else index.sec_filing_by_date
                timestamps = source.get(date_value)
                if timestamps is None or timestamps.size == 0:
                    continue
                left = np.searchsorted(timestamps, grid_start_ts[positions], side="left")
                right = np.searchsorted(timestamps, grid_end_ts[positions], side="left")
                labels_out[key][rows, horizon_index] = right > left
            timestamps_flags = index.condition_events.get((date_value, "flags"))
            if timestamps_flags is None:
                continue
            timestamps, flags = timestamps_flags
            if timestamps.size == 0:
                continue
            left = np.searchsorted(timestamps, grid_start_ts[positions], side="left")
            right = np.searchsorted(timestamps, grid_end_ts[positions], side="left")
            has_any = right > left
            if not bool(has_any.any()):
                continue
            for key in ("condition_halt_pause_flag", "condition_resume_flag", "condition_news_risk_flag", "condition_luld_limit_state_flag"):
                values = flags.get(key)
                if values is None:
                    continue
                for row, lval, rval, present in zip(rows, left, right, has_any):
                    if present:
                        labels_out[key][int(row), horizon_index] = bool(np.any(values[int(lval) : int(rval)]))

    def _intraday_compact_label_index(self, part: LoadedDailyIndexPart) -> IntradayCompactLabelIndex:
        key = id(part.context.get("intraday_base_bars"))
        with self._intraday_compact_label_lock:
            cached = self._intraday_compact_label_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_intraday_compact_label_index(part)
            self._intraday_compact_label_cache[key] = index
            return index

    def _label_context_index(self, part: LoadedDailyIndexPart, horizon_count: int) -> LabelContextIndex:
        key = (id(part.origins), id(part.labels), int(horizon_count))
        with self._label_index_lock:
            cached = self._label_index_cache.get(key)
            if cached is not None:
                return cached
            index = _prepare_label_context_index(part.origins, part.labels, int(horizon_count), strict=bool(self.config.strict_audit), part_key=_part_key(part.plan))
            self._label_index_cache[key] = index
            return index


class AsyncDailyIndexBatchLoader:
    def __init__(self, config: DailyIndexLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.index = DailyIndexCacheIndex(self.config)
        self.reader = DailyIndexPartReader(self.config.data_groups, include_external_context=self.config.include_external_context)
        self.materializer = DailyIndexBatchMaterializer(self.config)
        self.cache_manifest_fingerprint = _cache_plan_fingerprint(self.index.root_manifest, self.index.parts)
        self.dataset_plan_id = _dataset_plan_id(self.config, self.cache_manifest_fingerprint)
        seed = secrets.randbits(63) if self.config.randomize_seed else int(self.config.seed)
        ticker_packages = len({str(plan.package_dir) for plan in self.index.parts})
        ticker_count = len({str(plan.ticker) for plan in self.index.parts})
        self.state = DailyIndexLoaderState(
            dataset_plan_id=self.dataset_plan_id,
            cache_manifest_fingerprint=self.cache_manifest_fingerprint,
            seed=int(seed),
            epoch=0,
            total_available_origins=sum(int(plan.origin_count) for plan in self.index.parts),
            package_count=len(self.index.parts),
        )
        self._scanner_prefetched = False
        self._scanner_prefetch_profile: dict[str, float | int] = {}
        self._scanner_prefetch_lock = threading.Lock()
        self._scanner_prefetch_thread: threading.Thread | None = None
        self._scanner_prefetch_threads: list[threading.Thread] = []
        self._scanner_prefetch_inflight_paths: set[str] = set()
        self._stop_event = threading.Event()
        self._telemetry_lock = threading.Lock()
        self._telemetry: dict[str, Any] = {
            "loader_phase": "initialized",
            "part_count": int(len(self.index.parts)),
            "ticker_package_count": int(ticker_packages),
            "ticker_count": int(ticker_count),
            "total_available_origins": int(self.state.total_available_origins),
            "chronological_time_window_seconds": float(self.config.time_window_seconds),
            "batch_size": int(self.config.batch_size),
            "loaded_parts_per_group": int(self.config.loaded_parts_per_group),
        }

    def cancel(self) -> None:
        self._stop_event.set()

    def close(self) -> None:
        self.cancel()
        thread = self._scanner_prefetch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        for thread in list(self._scanner_prefetch_threads):
            if thread.is_alive():
                thread.join(timeout=2.0)

    def telemetry_snapshot(self) -> dict[str, Any]:
        with self._telemetry_lock:
            out = dict(self._telemetry)
        out.update(
            {
                "chronological_day_position": int(self.state.chronological_day_position),
                "chronological_origin_cursor": int(self.state.chronological_origin_cursor),
                "chronological_window_start_us": int(self.state.chronological_window_start_us),
                "emitted_batches": int(self.state.emitted_batches),
                "emitted_samples": int(self.state.emitted_samples),
                "seen_origins_total": int(self.state.seen_origins_total),
                "seen_origins_this_epoch": int(self.state.seen_origins_this_epoch),
            }
        )
        return out

    def _update_telemetry(self, **values: Any) -> None:
        with self._telemetry_lock:
            self._telemetry.update(values)
            self._telemetry["loader_telemetry_updated_at"] = float(time.time())

    def iter_batches(self) -> Iterator[DailyIndexTrainingBatch]:
        if self._stop_event.is_set():
            return
        if bool(self.config.chronological_replay):
            yield from self._iter_batches_chronological_replay()
            return
        if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
            return
        startup_profile = self.prefetch_scanner_indexes()
        start_us = _parse_timestamp_us(self.config.start_utc) if self.config.start_utc else None
        end_us = _parse_timestamp_us(self.config.end_utc) if self.config.end_utc else None
        group_size = max(1, int(self.config.loaded_parts_per_group))
        plans = self._epoch_plans(int(self.state.epoch))
        ready = _ReadyBatchBuffer(batch_size=int(self.config.batch_size), drop_last=bool(self.config.drop_last_batch))
        read_pool = ThreadPoolExecutor(max_workers=max(1, int(self.config.read_workers)), thread_name_prefix="tmc-load")
        try:
            group_start = int(self.state.package_position)
            while group_start < len(plans):
                if self._stop_event.is_set():
                    return
                self.state.package_position = int(group_start)
                group_end = self._next_group_end(plans, group_start, group_size)
                group_plans = plans[group_start:group_end]
                group_plan_count = len(group_plans)
                group_profile: dict[str, float] = {}
                stage_start = time.perf_counter()
                loaded_origins = _collect_ordered_futures(read_pool, self.reader.load_origins, group_plans, stop_event=self._stop_event)
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
                    group_start = int(group_end)
                    continue
                active_part_indices = sorted({int(ref.part_index) for ref in refs})
                part_index_map = {old_index: new_index for new_index, old_index in enumerate(active_part_indices)}
                stage_start = time.perf_counter()
                loaded = _collect_ordered_futures(read_pool, self.reader.load_payload, [loaded_origins[index] for index in active_part_indices], stop_event=self._stop_event)
                group_profile["payload_load_seconds"] = time.perf_counter() - stage_start
                refs = [
                    DailyIndexSampleRef(part_index=int(part_index_map[int(ref.part_index)]), origin_row=int(ref.origin_row))
                    for ref in refs
                ]
                group_keys = {_part_key(part.plan) for part in loaded}
                emitted_from_group = 0
                group_profile_attached = False
                materialize_size = int(self.config.materialize_chunk_size) or int(self.config.batch_size)
                try:
                    mat_pool = ThreadPoolExecutor(max_workers=max(1, int(self.config.materialize_workers)), thread_name_prefix="tmc-materialize")
                    try:
                        materialized = _materialize_bounded(
                            mat_pool,
                            self.materializer,
                            loaded,
                            _batched_refs(refs, materialize_size),
                            preserve_order=bool(self.config.preserve_batch_order),
                            stop_event=self._stop_event,
                        )
                        for chunk in materialized:
                            if self._stop_event.is_set():
                                return
                            chunk_ready_start = time.perf_counter()
                            if chunk.sample_count == 0:
                                continue
                            for batch in ready.add(chunk):
                                batch.profile.update(ready.telemetry())
                                batch.profile["ready_concat_seconds"] = float(batch.profile.get("ready_concat_seconds", 0.0)) + (time.perf_counter() - chunk_ready_start)
                                if not group_profile_attached:
                                    for key, value in group_profile.items():
                                        batch.profile[key] = float(batch.profile.get(key, 0.0)) + float(value)
                                    for key, value in startup_profile.items():
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
                        mat_pool.shutdown(wait=False, cancel_futures=True)
                finally:
                    self.materializer.clear_text_context_cache()
                    loaded = []
                    loaded_origins = []
                    refs = []
                    group_plans = []
                    gc.collect()
                self.state.origin_cursor = 0
                self.state.package_position = int(group_start) + int(group_plan_count)
                if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                    return
                group_start = int(group_end)
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
        finally:
            read_pool.shutdown(wait=False, cancel_futures=True)

    def _iter_batches_chronological_replay(self) -> Iterator[DailyIndexTrainingBatch]:
        if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
            return
        start_us = _parse_timestamp_us(self.config.start_utc) if self.config.start_utc else None
        end_us = _parse_timestamp_us(self.config.end_utc) if self.config.end_utc else None
        days = self._chronological_day_plans()
        ready = _ReadyBatchBuffer(batch_size=int(self.config.batch_size), drop_last=bool(self.config.drop_last_batch))
        read_pool = ThreadPoolExecutor(max_workers=max(1, int(self.config.read_workers)), thread_name_prefix="tmc-chron-load")
        mat_pool = ThreadPoolExecutor(max_workers=max(1, int(self.config.materialize_workers)), thread_name_prefix="tmc-chron-materialize")
        event_cache = _RollingEventStreamCache(config=self.config)
        context_cache = _RollingContextTensorCache(config=self.config)
        payload_cache: OrderedDict[str, LoadedDailyIndexPart] = OrderedDict()
        payload_cache_limit = max(1, int(self.config.loaded_parts_per_group))
        window_us = max(1, int(float(self.config.time_window_seconds) * 1_000_000.0))
        previous_day_key: str | None = None
        try:
            self._update_telemetry(loader_phase="planning_days", chronological_day_count=int(len(days)))
            for day_position in range(int(self.state.chronological_day_position), len(days)):
                if self._stop_event.is_set():
                    return
                source_date, day_plans = days[day_position]
                self.state.chronological_day_position = int(day_position)
                self._update_telemetry(
                    loader_phase="day_start",
                    chronological_day_position=int(day_position),
                    chronological_day_count=int(len(days)),
                    current_source_date=str(source_date),
                    day_package_count=int(len(day_plans)),
                    day_ticker_count=int(len({plan.ticker for plan in day_plans})),
                    payload_cache_parts=int(len(payload_cache)),
                )
                if previous_day_key is not None and not _chronological_days_are_adjacent(previous_day_key, source_date, days):
                    event_cache.clear()
                    self.materializer.clear_text_context_cache()
                    context_cache.clear()
                    payload_cache.clear()
                    self._update_telemetry(loader_phase="cache_reset", payload_cache_parts=0)
                previous_day_key = source_date
                day_start = time.perf_counter()
                self._update_telemetry(loader_phase="scanner_day_warm", current_source_date=str(source_date))
                scanner_day_profile = self.ensure_scanner_indexes_for_plans(day_plans, reason="current_day")
                self._update_telemetry(
                    loader_phase="scanner_day_ready",
                    current_source_date=str(source_date),
                    **scanner_day_profile,
                )
                if day_position + 1 < len(days):
                    next_source_date, next_day_plans = days[day_position + 1]
                    scanner_next_profile = self.prefetch_scanner_indexes_for_plans(next_day_plans, reason="next_day")
                    self._update_telemetry(
                        loader_phase="scanner_next_prefetch",
                        scanner_next_source_date=str(next_source_date),
                        **scanner_next_profile,
                    )
                else:
                    scanner_next_profile = {"scanner_next_day_prefetch_paths": int(0), "scanner_next_day_prefetch_total_paths": int(0)}
                day_plans = sorted(day_plans, key=lambda plan: (_plan_timestamp_start_us(plan), str(plan.ticker), int(plan.part_id)))
                day_bounds = _day_origin_timestamp_bounds(day_plans, start_us=start_us, end_us=end_us)
                if day_bounds is None:
                    self._update_telemetry(loader_phase="day_empty", current_source_date=str(source_date))
                    continue
                day_window_start, day_window_end = day_bounds
                if int(self.state.chronological_window_start_us) > 0:
                    window_start = max(int(self.state.chronological_window_start_us), int(day_window_start))
                else:
                    window_start = int(day_window_start)
                day_total_refs = int(sum(max(0, int(plan.origin_count)) for plan in day_plans))
                emitted_from_day = 0
                materialize_size = int(self.config.materialize_chunk_size) or min(int(self.config.batch_size), 256)
                self._update_telemetry(
                    loader_phase="day_planned",
                    current_source_date=str(source_date),
                    day_ticker_count=int(len({plan.ticker for plan in day_plans})),
                    day_refs_total=int(day_total_refs),
                    day_refs_remaining_before_window=int(day_total_refs),
                    day_start_timestamp_us=int(day_window_start),
                    day_end_timestamp_us=int(day_window_end),
                    materialize_chunk_size=int(materialize_size),
                    event_cache_capacity=int(self.config.ticker_cache_capacity),
                    origin_cursor_chunk_rows=int(self.config.origin_cursor_chunk_rows),
                    warm_all_ticker_caches=int(bool(self.config.warm_all_ticker_caches)),
                )
                cursor_start = time.perf_counter()
                cursors = _build_day_origin_cursors(day_plans, chunk_rows=int(self.config.origin_cursor_chunk_rows))
                cursor_profile = _load_origin_cursor_initial_chunks(
                    read_pool=read_pool,
                    cursors=cursors,
                    stop_event=self._stop_event,
                )
                self._update_telemetry(
                    loader_phase="origin_cursors_ready",
                    **cursor_profile,
                    **event_cache.telemetry_snapshot(),
                )
                day_warm_profile: dict[str, float | int] = {
                    "cache_first_cursor_build_seconds": time.perf_counter() - cursor_start,
                    **cursor_profile,
                    **scanner_day_profile,
                    **scanner_next_profile,
                }
                if bool(self.config.warm_all_ticker_caches):
                    self._update_telemetry(loader_phase="cache_warm_event", event_cache_warm_tickers=0, event_cache_warm_total_tickers=int(len(cursors)))
                    warm_profile = _warm_event_cache_for_day(
                        read_pool=read_pool,
                        event_cache=event_cache,
                        cursors=cursors,
                        first_timestamp_us=int(window_start),
                        stop_event=self._stop_event,
                        telemetry_callback=self._update_telemetry,
                    )
                    day_warm_profile.update(warm_profile)
                    self._update_telemetry(
                        loader_phase="cache_warmed_day",
                        **warm_profile,
                        **event_cache.telemetry_snapshot(),
                    )
                    if _has_runtime_context_groups(self.config):
                        self._update_telemetry(
                            loader_phase="cache_warm_context",
                            context_cache_warm_payload_tickers=0,
                            context_cache_warm_payload_total_tickers=int(len(cursors)),
                        )
                        context_day_warm_profile = _warm_context_cache_for_day(
                            read_pool=read_pool,
                            reader=self.reader,
                            materializer=self.materializer,
                            context_cache=context_cache,
                            cursors=cursors,
                            first_timestamp_us=int(window_start),
                            stop_event=self._stop_event,
                            telemetry_callback=self._update_telemetry,
                        )
                        day_warm_profile.update(context_day_warm_profile)
                        self._update_telemetry(
                            loader_phase="cache_warmed_context_day",
                            **context_day_warm_profile,
                            **context_cache.telemetry_snapshot(),
                        )
                frontier_ref_cap = _chronological_frontier_ref_cap(self.config, materialize_size=int(materialize_size))
                while window_start < int(day_window_end):
                    if self._stop_event.is_set():
                        return
                    window_end = min(int(window_start) + int(window_us), int(day_window_end))
                    self.state.chronological_window_start_us = int(window_start)
                    after_key = self._frontier_after_key()
                    self._update_telemetry(
                        loader_phase="frontier_plan",
                        current_source_date=str(source_date),
                        window_start_timestamp_us=int(window_start),
                        window_end_timestamp_us=int(window_end),
                        origin_frontier_cap_origins=int(frontier_ref_cap),
                        origin_frontier_after_timestamp_us=int(after_key[0]) if after_key is not None else 0,
                        origin_frontier_after_ordinal=int(after_key[2]) if after_key is not None else 0,
                        day_refs_remaining_before_window=int(max(0, int(day_total_refs) - int(emitted_from_day))),
                    )
                    self._update_telemetry(
                        loader_phase="frontier_read",
                        origin_cursor_count=int(len(cursors)),
                    )
                    origin_window_start = time.perf_counter()
                    loaded_origins, window_refs, cursor_window_profile = _load_frontier_origins_from_cursors(
                        cursors=cursors,
                        start_us=int(window_start),
                        end_us=int(window_end),
                        after_key=after_key,
                        max_refs=int(frontier_ref_cap),
                        config=self.config,
                        seed=int(self.state.seed),
                        dataset_plan_id=self.dataset_plan_id,
                        stop_event=self._stop_event,
                    )
                    origin_window_load_seconds = time.perf_counter() - origin_window_start
                    self._update_telemetry(
                        loader_phase="frontier_ready",
                        origin_window_load_seconds=float(origin_window_load_seconds),
                        **cursor_window_profile,
                    )
                    if not loaded_origins or not window_refs:
                        self.state.chronological_origin_cursor = 0
                        self._clear_frontier_after()
                        self._update_telemetry(loader_phase="frontier_empty", window_active_parts=0, window_active_tickers=0)
                        window_start = int(window_end)
                        continue
                    first_ts = int(_ref_origin_timestamp(loaded_origins, window_refs[0]))
                    last_ts = int(_ref_origin_timestamp(loaded_origins, window_refs[-1]))
                    active_indices = sorted({int(ref.part_index) for ref in window_refs})
                    self._update_telemetry(
                        loader_phase="payload_load",
                        window_active_refs=int(len(window_refs)),
                        window_active_parts=int(len(active_indices)),
                        window_active_tickers=int(len({loaded_origins[int(index)].plan.ticker for index in active_indices})),
                    )
                    loaded_parts = _load_active_payloads(
                        read_pool=read_pool,
                        reader=self.reader,
                        loaded_origins=loaded_origins,
                        active_indices=active_indices,
                        payload_cache=payload_cache,
                        payload_cache_limit=payload_cache_limit,
                        stop_event=self._stop_event,
                    )
                    self._update_telemetry(
                        loader_phase="payload_ready",
                        payload_cache_parts=int(len(payload_cache)),
                        payload_cache_limit=int(payload_cache_limit),
                    )
                    part_index_map = {old_index: new_index for new_index, old_index in enumerate(active_indices)}
                    remapped_refs = [
                        DailyIndexSampleRef(part_index=int(part_index_map[int(ref.part_index)]), origin_row=int(ref.origin_row))
                        for ref in window_refs
                    ]
                    self._update_telemetry(loader_phase="cache_warm")
                    warm_profile = event_cache.warm(loaded_parts, remapped_refs)
                    context_warm_profile = context_cache.warm(self.materializer, loaded_parts, remapped_refs)
                    self._update_telemetry(
                        loader_phase="cache_warmed",
                        **warm_profile,
                        **context_warm_profile,
                        **event_cache.telemetry_snapshot(),
                        **context_cache.telemetry_snapshot(),
                    )
                    materialized = _materialize_chronological_bounded(
                        mat_pool,
                        self.materializer,
                        loaded_parts,
                        _batched_refs(remapped_refs, materialize_size),
                        event_cache=event_cache,
                        context_cache=context_cache,
                        preserve_order=True,
                        stop_event=self._stop_event,
                        telemetry_callback=self._update_telemetry,
                        startup_pending_chunks=max(
                            1,
                            (int(self.config.batch_size) + int(materialize_size) - 1) // max(1, int(materialize_size)),
                        ),
                        day_profile={
                            "chronological_replay": int(1),
                            "chronological_day_load_seconds": time.perf_counter() - day_start,
                            "chronological_time_window_seconds": float(self.config.time_window_seconds),
                            "window_active_refs": int(len(window_refs)),
                            "window_active_parts": int(len(active_indices)),
                            "window_active_tickers": int(len({loaded_origins[int(index)].plan.ticker for index in active_indices})),
                            "window_start_timestamp_us": int(first_ts),
                            "window_end_timestamp_us": int(last_ts),
                            "day_refs_total": int(day_total_refs),
                            "day_refs_remaining_before_window": int(max(0, int(day_total_refs) - int(emitted_from_day))),
                            "origin_window_load_seconds": float(origin_window_load_seconds),
                            "origin_cache_parts": int(len(loaded_origins)),
                            "origin_cache_limit": int(len(cursors)),
                            "payload_cache_parts": int(len(payload_cache)),
                            "payload_cache_limit": int(payload_cache_limit),
                            "origin_window_sort_seconds": float(cursor_window_profile.get("origin_window_sort_seconds", 0.0)),
                            **day_warm_profile,
                            **cursor_window_profile,
                            **event_cache.telemetry_snapshot(),
                            **context_cache.telemetry_snapshot(),
                            **self.materializer.telemetry_snapshot(),
                        },
                    )
                    self._update_telemetry(loader_phase="materialize")
                    for chunk in materialized:
                        if self._stop_event.is_set():
                            return
                        if chunk.sample_count == 0:
                            continue
                        for batch in ready.add(chunk):
                            batch.profile.update(ready.telemetry())
                            batch.profile.update(event_cache.telemetry_snapshot())
                            batch.profile.update(context_cache.telemetry_snapshot())
                            batch.profile.update(self.materializer.telemetry_snapshot())
                            self._update_telemetry(
                                loader_phase="batch_ready",
                                ready_buffer_chunks=int(ready.telemetry().get("ready_buffer_chunks", 0)),
                                ready_buffer_samples=int(ready.telemetry().get("ready_buffer_samples", 0)),
                                **event_cache.telemetry_snapshot(),
                                **context_cache.telemetry_snapshot(),
                                **self.materializer.telemetry_snapshot(),
                            )
                            batch = self._apply_epoch_sample_cap(batch)
                            if batch.sample_count == 0:
                                return
                            emitted_from_day += int(batch.sample_count)
                            self.state.chronological_origin_cursor += int(batch.sample_count)
                            self._set_frontier_after_from_batch(batch)
                            self._record_emitted_batch(batch)
                            yield batch
                            if int(self.config.max_batches) > 0 and int(self.state.emitted_batches) >= int(self.config.max_batches):
                                return
                            if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                                return
                    if int(cursor_window_profile.get("origin_frontier_cap_reached", 0) or 0):
                        self._update_telemetry(
                            loader_phase="frontier_cap_continue",
                            window_start_timestamp_us=int(window_start),
                            window_end_timestamp_us=int(window_end),
                            chronological_origin_cursor=int(self.state.chronological_origin_cursor),
                            origin_frontier_cap_origins=int(frontier_ref_cap),
                        )
                    else:
                        self.state.chronological_origin_cursor = 0
                        self._clear_frontier_after()
                        window_start = int(window_end)
                self.state.chronological_origin_cursor = 0
                self.state.chronological_window_start_us = 0
                self._clear_frontier_after()
            for batch in ready.flush():
                batch = self._apply_epoch_sample_cap(batch)
                if batch.sample_count == 0:
                    return
                self._set_frontier_after_from_batch(batch)
                self._record_emitted_batch(batch)
                yield batch
                if int(self.config.max_batches) > 0 and int(self.state.emitted_batches) >= int(self.config.max_batches):
                    return
                if int(self.config.max_origins_per_epoch) > 0 and int(self.state.seen_origins_this_epoch) >= int(self.config.max_origins_per_epoch):
                    return
            self.state.completed_epochs += 1
            self.state.epoch += 1
            self.state.chronological_day_position = 0
            self.state.chronological_origin_cursor = 0
            self.state.chronological_window_start_us = 0
            self._clear_frontier_after()
            self.state.seen_origins_this_epoch = 0
        finally:
            read_pool.shutdown(wait=False, cancel_futures=True)
            mat_pool.shutdown(wait=False, cancel_futures=True)

    def _frontier_after_key(self) -> tuple[int, str, int] | None:
        timestamp_us = int(self.state.chronological_frontier_after_timestamp_us)
        if timestamp_us <= 0:
            return None
        return timestamp_us, str(self.state.chronological_frontier_after_ticker), int(self.state.chronological_frontier_after_ordinal)

    def _set_frontier_after_from_batch(self, batch: DailyIndexTrainingBatch) -> None:
        if int(batch.sample_count) <= 0:
            return
        row = int(batch.sample_count) - 1
        self.state.chronological_frontier_after_timestamp_us = int(batch.origin_timestamp_us[row])
        self.state.chronological_frontier_after_ticker = str(batch.ticker[row])
        self.state.chronological_frontier_after_ordinal = int(batch.origin_ordinal[row])

    def _clear_frontier_after(self) -> None:
        self.state.chronological_frontier_after_timestamp_us = 0
        self.state.chronological_frontier_after_ticker = ""
        self.state.chronological_frontier_after_ordinal = 0

    def _chronological_day_plans(self) -> list[tuple[str, list[DailyIndexPartPlan]]]:
        plans = sorted(self.index.parts, key=lambda plan: (str(plan.source_date), str(plan.month), str(plan.ticker), int(plan.part_id)))
        days: OrderedDict[str, list[DailyIndexPartPlan]] = OrderedDict()
        for plan in plans:
            days.setdefault(str(plan.source_date), []).append(plan)
        return list(days.items())

    def ensure_scanner_indexes_for_plans(self, plans: Sequence[DailyIndexPartPlan], *, reason: str = "current_day") -> dict[str, float | int]:
        paths = self._scanner_paths_for_plans(plans)
        profile = self._build_scanner_indexes_for_paths(paths, reason=reason)
        with self._scanner_prefetch_lock:
            self._scanner_prefetch_profile = dict(profile)
        return profile

    def prefetch_scanner_indexes_for_plans(self, plans: Sequence[DailyIndexPartPlan], *, reason: str = "next_day") -> dict[str, float | int]:
        if "scanner_context" not in self.config.data_groups or not bool(self.config.prefetch_scanner_indexes):
            return {f"scanner_{reason}_prefetch_enabled": int(0)}
        paths = self._scanner_paths_for_plans(plans)
        candidates: list[Path] = []
        with self._scanner_prefetch_lock:
            for path in paths:
                key = str(path.resolve())
                if key in self._scanner_prefetch_inflight_paths:
                    continue
                if self.materializer.has_scanner_artifact_index(key):
                    continue
                self._scanner_prefetch_inflight_paths.add(key)
                candidates.append(path)
        profile: dict[str, float | int] = {
            f"scanner_{reason}_prefetch_enabled": int(1),
            f"scanner_{reason}_prefetch_async": int(1),
            f"scanner_{reason}_prefetch_paths": int(len(candidates)),
            f"scanner_{reason}_prefetch_total_paths": int(len(paths)),
        }
        if not candidates:
            profile[f"scanner_{reason}_prefetch_done"] = int(1)
            with self._scanner_prefetch_lock:
                self._scanner_prefetch_profile = dict(profile)
            return profile

        def run_background() -> None:
            try:
                update = self._build_scanner_indexes_for_paths(candidates, reason=f"{reason}_prefetch")
                update[f"scanner_{reason}_prefetch_done"] = int(1)
            except Exception:  # noqa: BLE001
                update = {
                    **profile,
                    f"scanner_{reason}_prefetch_done": int(0),
                    f"scanner_{reason}_prefetch_failed": int(1),
                }
            finally:
                with self._scanner_prefetch_lock:
                    for path in candidates:
                        self._scanner_prefetch_inflight_paths.discard(str(path.resolve()))
                    self._scanner_prefetch_profile = dict(update)
                self._update_telemetry(**update)

        thread = threading.Thread(target=run_background, name=f"tmc-scanner-{reason}-prefetch", daemon=True)
        self._scanner_prefetch_threads.append(thread)
        thread.start()
        with self._scanner_prefetch_lock:
            self._scanner_prefetch_profile = dict(profile)
        return profile

    def _scanner_paths_for_plans(self, plans: Sequence[DailyIndexPartPlan]) -> list[Path]:
        if "scanner_context" not in self.config.data_groups or not bool(self.config.prefetch_scanner_indexes):
            return []
        paths: dict[str, Path] = {}
        for plan in plans:
            source_date = str(plan.source_date or "")[:10]
            if not source_date:
                continue
            path = _scanner_context_file(_month_global_dir(plan.package_dir), source_date)
            paths[str(path.resolve())] = path
        return [paths[key] for key in sorted(paths)]

    def _build_scanner_indexes_for_paths(self, paths: Sequence[Path], *, reason: str) -> dict[str, float | int]:
        started = time.perf_counter()
        if "scanner_context" not in self.config.data_groups or not bool(self.config.prefetch_scanner_indexes):
            return {f"scanner_{reason}_enabled": int(0)}
        pl = _polars()
        built = 0
        reused = 0
        missing = 0
        empty = 0
        failed = 0
        for path in paths:
            if self._stop_event.is_set():
                break
            key = str(path.resolve())
            if self.materializer.has_scanner_artifact_index(key):
                reused += 1
                continue
            if not path.exists():
                missing += 1
                continue
            try:
                frame = pl.read_parquet(path)
                if int(getattr(frame, "height", 0) or 0) <= 0:
                    empty += 1
                    continue
                self.materializer._scanner_artifact_index(frame, cache_key=key)  # noqa: SLF001
                built += 1
            except Exception:  # noqa: BLE001
                failed += 1
        return {
            f"scanner_{reason}_seconds": float(time.perf_counter() - started),
            f"scanner_{reason}_paths": int(len(paths)),
            f"scanner_{reason}_built": int(built),
            f"scanner_{reason}_reused": int(reused),
            f"scanner_{reason}_missing": int(missing),
            f"scanner_{reason}_empty": int(empty),
            f"scanner_{reason}_failed": int(failed),
        }

    def state_dict(self) -> dict[str, Any]:
        return self.state.to_dict()

    def load_state_dict(self, value: Mapping[str, Any]) -> None:
        state = DailyIndexLoaderState.from_dict(value)
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

    def prefetch_scanner_indexes(self) -> dict[str, float | int]:
        if self._scanner_prefetched:
            with self._scanner_prefetch_lock:
                return dict(self._scanner_prefetch_profile)
        self._scanner_prefetched = True
        if "scanner_context" not in self.config.data_groups or not bool(self.config.prefetch_scanner_indexes):
            return {"scanner_prefetch_enabled": int(0)}
        scanner_paths = sorted(
            {
                _scanner_context_file(_month_global_dir(plan.package_dir), str(plan.source_date))
                for plan in self.index.parts
                if str(plan.source_date).strip()
            }
        )
        if not scanner_paths:
            return {"scanner_prefetch_enabled": int(1), "scanner_prefetch_files": int(0)}
        cache_limit = max(1, int(self.config.scanner_index_cache_entries))
        total_scanner_paths = len(scanner_paths)
        scanner_paths = scanner_paths[:cache_limit]
        started = time.perf_counter()
        workers = max(1, int(self.config.scanner_prefetch_workers))
        profile: dict[str, float | int] = {
            "scanner_prefetch_enabled": int(1),
            "scanner_prefetch_async": int(1),
            "scanner_prefetch_started": int(1),
            "scanner_prefetch_files": int(len(scanner_paths)),
            "scanner_prefetch_total_files": int(total_scanner_paths),
            "scanner_prefetch_workers": int(workers),
        }
        with self._scanner_prefetch_lock:
            self._scanner_prefetch_profile = dict(profile)

        def run_background() -> None:
            pl = _polars()
            built = 0
            missing = 0

            def build(path: Path) -> bool:
                if not path.exists():
                    return False
                frame = pl.read_parquet(path)
                if int(getattr(frame, "height", 0) or 0) <= 0:
                    return False
                self.materializer._scanner_artifact_index(frame, cache_key=str(path.resolve()))  # noqa: SLF001
                return True

            try:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tmc-scanner-prefetch") as pool:
                    for ok in pool.map(build, scanner_paths):
                        if ok:
                            built += 1
                        else:
                            missing += 1
                update: dict[str, float | int] = {
                    **profile,
                    "scanner_prefetch_seconds": float(time.perf_counter() - started),
                    "scanner_prefetch_built": int(built),
                    "scanner_prefetch_missing": int(missing),
                    "scanner_prefetch_done": int(1),
                }
            except Exception as exc:  # noqa: BLE001
                update = {
                    **profile,
                    "scanner_prefetch_seconds": float(time.perf_counter() - started),
                    "scanner_prefetch_done": int(0),
                    "scanner_prefetch_failed": int(1),
                }
            with self._scanner_prefetch_lock:
                self._scanner_prefetch_profile = update

        self._scanner_prefetch_thread = threading.Thread(target=run_background, name="tmc-scanner-prefetch-bg", daemon=True)
        self._scanner_prefetch_thread.start()
        return profile

    def summary(self) -> dict[str, Any]:
        out = self.state.to_dict()
        out["epoch_fraction"] = float(self.state.package_position) / max(float(len(self.index.parts)), 1.0)
        out["part_count"] = int(len(self.index.parts))
        out["ticker_package_count"] = int(len({str(plan.package_dir) for plan in self.index.parts}))
        out["ticker_count"] = int(len({str(plan.ticker) for plan in self.index.parts}))
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
            "chronological_replay": bool(self.config.chronological_replay),
            "time_window_seconds": float(self.config.time_window_seconds),
            "frontier_max_origins_per_window": int(self.config.frontier_max_origins_per_window),
            "ticker_cache_capacity": int(self.config.ticker_cache_capacity),
            "origin_cursor_chunk_rows": int(self.config.origin_cursor_chunk_rows),
            "warm_all_ticker_caches": bool(self.config.warm_all_ticker_caches),
        }
        return out

    def _epoch_plans(self, epoch: int) -> list[DailyIndexPartPlan]:
        plans = list(self.index.parts)
        if self.config.shuffle_parts:
            random.Random(_stable_int_seed("packages", self.state.seed, epoch, self.dataset_plan_id)).shuffle(plans)
        else:
            plans.sort(key=lambda plan: (str(plan.source_date), str(plan.month), str(plan.ticker), int(plan.part_id)))
        return plans

    def _next_group_end(self, plans: Sequence[DailyIndexPartPlan], group_start: int, group_size: int) -> int:
        return min(len(plans), int(group_start) + max(1, int(group_size)))

    def _record_emitted_batch(self, batch: DailyIndexTrainingBatch) -> None:
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

    def _apply_epoch_sample_cap(self, batch: DailyIndexTrainingBatch) -> DailyIndexTrainingBatch:
        cap = int(self.config.max_origins_per_epoch)
        if cap <= 0:
            return batch
        remaining = cap - int(self.state.seen_origins_this_epoch)
        if remaining <= 0:
            return _slice_training_batch(batch, 0, 0)
        if batch.sample_count <= remaining:
            return batch
        return _slice_training_batch(batch, 0, remaining)


def normalize_loader_config(config: DailyIndexLoaderConfig) -> DailyIndexLoaderConfig:
    mode = str(config.event_output_mode)
    if mode not in SUPPORTED_EVENT_OUTPUT_MODES:
        raise ValueError(f"Unsupported event_output_mode={mode!r}; expected one of {sorted(SUPPORTED_EVENT_OUTPUT_MODES)}")
    groups = tuple(dict.fromkeys(TEXT_CONTEXT_GROUP_ALIASES.get(str(group), str(group)) for group in config.data_groups))
    if mode in {"raw_flat", "raw_stream", "raw_windows"} and "events" not in groups:
        groups = (*groups, "events")
    if mode == "encoded_uint8":
        groups = (*tuple(group for group in groups if group != "encoded_events"), "events", "encoded_events")
    return DailyIndexLoaderConfig(
        cache_root=Path(config.cache_root),
        split=str(config.split),
        start_utc=str(config.start_utc),
        end_utc=str(config.end_utc),
        months=tuple(str(month) for month in config.months),
        tickers=tuple(str(ticker) for ticker in config.tickers),
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
        corporate_action_max_items=max(0, int(config.corporate_action_max_items)),
        corporate_action_label_days=tuple(max(1, int(value)) for value in config.corporate_action_label_days),
        intraday_label_horizons=tuple(str(value).strip() for value in config.intraday_label_horizons if str(value).strip()),
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
        validate_time_feature_contract=bool(config.validate_time_feature_contract),
        scanner_groups=tuple(str(group) for group in config.scanner_groups),
        scanner_horizons=tuple(str(horizon) for horizon in config.scanner_horizons),
        scanner_top_k=max(1, int(config.scanner_top_k)),
        scanner_required=bool(config.scanner_required),
        scanner_index_cache_entries=max(0, int(config.scanner_index_cache_entries)),
        prefetch_scanner_indexes=bool(config.prefetch_scanner_indexes),
        scanner_prefetch_workers=max(1, int(config.scanner_prefetch_workers)),
        days=tuple(str(day)[:10] for day in config.days if str(day).strip()),
        chronological_replay=bool(config.chronological_replay),
        time_window_seconds=max(0.001, float(config.time_window_seconds)),
        frontier_max_origins_per_window=max(0, int(config.frontier_max_origins_per_window)),
        ticker_cache_capacity=max(1, int(config.ticker_cache_capacity)),
        origin_cursor_chunk_rows=max(1, int(config.origin_cursor_chunk_rows)),
        warm_all_ticker_caches=bool(config.warm_all_ticker_caches),
    )


def _read_root_manifest(cache_root: Path) -> dict[str, Any]:
    path = Path(cache_root) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing daily-index cache manifest: {path}")
    manifest = read_json(path)
    if manifest.get("cache_format") != DAILY_INDEX_CACHE_FORMAT:
        raise ValueError(f"Unexpected cache format: {manifest.get('cache_format')!r}")
    if int(manifest.get("cache_version") or 0) != DAILY_INDEX_CACHE_VERSION:
        raise ValueError(f"Unexpected cache version: {manifest.get('cache_version')!r}")
    return manifest


def _selected_months(config: DailyIndexLoaderConfig, manifest: Mapping[str, Any]) -> tuple[str, ...]:
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


def _plan_file_path(plan: DailyIndexPartPlan, value: str) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    cache_relative = Path(plan.cache_root) / path
    if cache_relative.exists() or str(path).startswith("month="):
        return cache_relative
    return Path(plan.package_dir) / path


def _global_context_file(global_dir: Path, key: str) -> Path:
    manifest_path = Path(global_dir) / "manifest.json"
    if not manifest_path.exists():
        return Path(global_dir) / f"{key}.parquet"
    files = _package_context_files_from_manifest(read_json(manifest_path))
    value = files.get(str(key))
    if not value and str(key) == "global_daily_bars":
        value = files.get("daily_bars")
    if not value:
        return Path(global_dir) / f"{key}.parquet"
    path = Path(str(value))
    if path.is_absolute():
        return path
    if str(path).startswith("month="):
        month_dir = Path(global_dir).parent
        cache_root = month_dir.parent if month_dir.name.startswith("month=") else Path(global_dir).parent
        return cache_root / path
    return Path(global_dir) / path


def _scanner_context_file(global_dir: Path, source_date: str) -> Path:
    date_text = str(source_date)[:10]
    manifest_path = Path(global_dir) / "manifest.json"
    if manifest_path.exists():
        files = _package_context_files_from_manifest(read_json(manifest_path))
        value = files.get(f"scanner_context_{date_text}") or files.get("scanner_context")
        if value:
            path = Path(str(value))
            if path.is_absolute():
                return path
            if str(path).startswith("month="):
                month_dir = Path(global_dir).parent
                cache_root = month_dir.parent if month_dir.name.startswith("month=") else Path(global_dir).parent
                return cache_root / path
            return Path(global_dir) / path
    return Path(global_dir) / "scanner" / f"scanner_{date_text}.parquet"


def _package_context_files(package_dir: Path) -> dict[str, str]:
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path)
    return _package_context_files_from_manifest(manifest)


def _source_date_from_part(part: Mapping[str, Any]) -> str:
    raw = str(part.get("source_date") or "")
    if raw:
        return raw[:10]
    job_id = str(part.get("job_id") or "")
    for token in job_id.split("|"):
        if len(token) >= 10 and token[4:5] == "-" and token[7:8] == "-":
            return token[:10]
    return ""


def _intraday_context_files_by_source_date(manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for part in manifest.get("modality_parts") or ():
        if not isinstance(part, Mapping):
            continue
        source_date = _source_date_from_part(part)
        if not source_date:
            continue
        paths = dict(part.get("output_paths") or {})
        selected = {
            str(key): str(value)
            for key, value in paths.items()
            if str(key) in {"intraday_base_bars", "intraday_condition_events"} and str(value)
        }
        if not selected:
            continue
        out.setdefault(source_date, {}).update(selected)
    return out


def _package_context_files_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    raw_files = dict(manifest.get("files") or {})
    for part in manifest.get("modality_parts") or ():
        if not isinstance(part, Mapping):
            continue
        for key, value in dict(part.get("output_paths") or {}).items():
            raw_files[str(key)] = str(value)
        path = str(part.get("event_path") or "")
        if path:
            raw_files[str(part.get("data_group") or part.get("modality") or Path(path).stem)] = path
    for key, value in raw_files.items():
        normalized = TEXT_CONTEXT_GROUP_ALIASES.get(str(key), str(key))
        normalized = {
            "news_embeddings": "ticker_news_embeddings",
            "sec_embeddings": "sec_filing_embeddings",
            "macro_bars": "daily_bars",
            "global_macro_bars": "global_daily_bars",
        }.get(normalized, normalized)
        if normalized in {
            "ticker_news_embeddings",
            "market_news_embeddings",
            "sec_filing_embeddings",
            "xbrl",
            "daily_bars",
            "global_daily_bars",
            "corporate_actions",
            "intraday_base_bars",
            "intraday_condition_events",
            "scanner_context",
        }:
            out[normalized] = str(value)
        elif str(normalized).startswith("scanner_context_"):
            out[normalized] = str(value)
    return out


def _text_group_limits(config: DailyIndexLoaderConfig, group: str) -> tuple[int, int]:
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
    fiscal_period_id = _category_id_column_or_map(frame, "fiscal_period_id", "fiscal_period", "fiscal_period", category_index)[order]
    calendar_period_id = _category_id_column_or_map(frame, "calendar_period_id", "calendar_period_code", "calendar_period_code", category_index)[order]
    taxonomy_id = _category_id_column_or_map(frame, "taxonomy_id", "taxonomy", "taxonomy", category_index)[order]
    tag_id = _category_id_column_or_map(frame, "tag_id", "tag", "tag", category_index)[order]
    unit_id = _category_id_column_or_map(frame, "unit_id", "unit_code", "unit_code", category_index)[order]
    form_id = _category_id_column_or_map(frame, "form_id", "form_type", "form_type", category_index)[order]
    row_kind_id = _category_id_column_or_map(frame, "row_kind_id", "xbrl_row_kind", "xbrl_row_kind", category_index)[order]
    location_id = _category_id_column_or_map(frame, "location_id", "location_code", "location_code", category_index)[order]
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


def _prepare_corporate_action_context_index(frame: Any) -> CorporateActionContextIndex:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0 or "available_timestamp_us" not in getattr(frame, "columns", ()):
        return _empty_corporate_action_context_index()
    available = _frame_column_as(frame, "available_timestamp_us", np.int64, 0)
    effective = _frame_column_as(frame, "effective_timestamp_us", np.int64, 0)
    order = np.lexsort((effective, available))
    available = available[order]
    effective = effective[order]
    available_time = _cached_or_computed_time_feature_matrix(frame, CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS, _frame_column_as(frame, "available_timestamp_us", np.int64, 0))[order]
    effective_time = _cached_or_computed_time_feature_matrix(frame, CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS, _frame_column_as(frame, "effective_timestamp_us", np.int64, 0))[order]
    numeric_columns = []
    for key in CORPORATE_ACTION_NUMERIC_FEATURE_KEYS:
        numeric_columns.append(_frame_column_as(frame, key, np.float32, 0.0)[order])
    numeric = np.stack(numeric_columns, axis=-1).astype(np.float32, copy=False) if numeric_columns else np.zeros((int(available.shape[0]), 0), dtype=np.float32)
    return CorporateActionContextIndex(
        available_timestamps_us=available,
        effective_timestamps_us=effective,
        action_type_id=_frame_column_as(frame, "action_type_id", np.uint32, 0)[order],
        dividend_type_id=_frame_column_as(frame, "dividend_type_id", np.uint32, 0)[order],
        currency_id=_frame_column_as(frame, "currency_id", np.uint32, 0)[order],
        frequency_id=_frame_column_as(frame, "frequency_id", np.uint32, 0)[order],
        numeric_features=numeric,
        available_time_features=available_time.astype(np.float32, copy=False),
        effective_time_features=effective_time.astype(np.float32, copy=False),
        effective_epoch_day=_frame_column_as(frame, "effective_epoch_day", np.int32, 0)[order],
        declaration_epoch_day=_frame_column_as(frame, "declaration_epoch_day", np.int32, 0)[order],
        pay_epoch_day=_frame_column_as(frame, "pay_epoch_day", np.int32, 0)[order],
        record_epoch_day=_frame_column_as(frame, "record_epoch_day", np.int32, 0)[order],
    )


def _empty_corporate_action_context_index() -> CorporateActionContextIndex:
    return CorporateActionContextIndex(
        available_timestamps_us=np.zeros((0,), dtype=np.int64),
        effective_timestamps_us=np.zeros((0,), dtype=np.int64),
        action_type_id=np.zeros((0,), dtype=np.uint32),
        dividend_type_id=np.zeros((0,), dtype=np.uint32),
        currency_id=np.zeros((0,), dtype=np.uint32),
        frequency_id=np.zeros((0,), dtype=np.uint32),
        numeric_features=np.zeros((0, len(CORPORATE_ACTION_NUMERIC_FEATURE_KEYS)), dtype=np.float32),
        available_time_features=np.zeros((0, len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)), dtype=np.float32),
        effective_time_features=np.zeros((0, len(CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS)), dtype=np.float32),
        effective_epoch_day=np.zeros((0,), dtype=np.int32),
        declaration_epoch_day=np.zeros((0,), dtype=np.int32),
        pay_epoch_day=np.zeros((0,), dtype=np.int32),
        record_epoch_day=np.zeros((0,), dtype=np.int32),
    )


def _select_corporate_action_item_indices(index: CorporateActionContextIndex, origin_timestamps_us: np.ndarray, *, max_items: int) -> tuple[np.ndarray, np.ndarray]:
    origins = np.asarray(origin_timestamps_us, dtype=np.int64)
    max_items = max(0, int(max_items))
    if max_items <= 0 or index.item_count <= 0 or origins.shape[0] <= 0:
        return np.full((int(origins.shape[0]), max_items), -1, dtype=np.int64), np.zeros((int(origins.shape[0]), max_items), dtype=np.bool_)
    rightmost = np.searchsorted(index.available_timestamps_us, origins, side="right") - 1
    offsets = np.arange(max_items, dtype=np.int64)
    indices = rightmost[:, None] - offsets[None, :]
    valid = indices >= 0
    return np.where(valid, indices, -1).astype(np.int64, copy=False), valid


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


def _category_id_column_or_map(
    frame: Any,
    id_column: str,
    value_column: str,
    field_name: str,
    category_index: XbrlCategoryReferenceIndex,
) -> np.ndarray:
    if id_column in getattr(frame, "columns", ()):
        return _frame_column_as(frame, id_column, np.uint32, 0)
    return _map_category_ids(_optional_frame_column(frame, value_column, ""), field_name, category_index)


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


def _bar_time_feature_matrix(timestamps_us: np.ndarray, origins_us: np.ndarray) -> np.ndarray:
    source = np.asarray(timestamps_us, dtype=np.int64)
    origin = np.asarray(origins_us, dtype=np.int64)
    source, origin = np.broadcast_arrays(source, origin)
    absolute = _absolute_utc_time_feature_matrix(source.reshape(-1)).reshape((*source.shape, -1))
    age_days = np.maximum(0.0, (origin.astype(np.float64) - source.astype(np.float64)) / 86_400_000_000.0).astype(np.float32)
    return np.concatenate(
        [absolute, np.stack([age_days, np.log1p(age_days.astype(np.float64)).astype(np.float32)], axis=-1)],
        axis=-1,
    ).astype(np.float32, copy=False)


def _bar_end_time_feature_matrix(timestamps_us: np.ndarray, origins_us: np.ndarray) -> np.ndarray:
    source = np.asarray(timestamps_us, dtype=np.int64)
    origin = np.asarray(origins_us, dtype=np.int64)
    source, origin = np.broadcast_arrays(source, origin)
    absolute = _absolute_utc_time_feature_matrix(source.reshape(-1)).reshape((*source.shape, -1))
    age_days = np.maximum(0.0, (origin.astype(np.float64) - source.astype(np.float64)) / 86_400_000_000.0).astype(np.float32)
    return np.concatenate(
        [absolute, np.stack([age_days, np.log1p(age_days.astype(np.float64)).astype(np.float32)], axis=-1)],
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


def _process_rss_mib() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return _process_rss_mib_windows()


def _process_rss_mib_windows() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
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

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        if not ok:
            return 0.0
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _sample_refs_for_loaded_parts(
    loaded: Sequence[LoadedDailyIndexPart],
    *,
    config: DailyIndexLoaderConfig,
    seed: int,
    dataset_plan_id: str,
    start_us: int | None,
    end_us: int | None,
) -> list[DailyIndexSampleRef]:
    refs: list[DailyIndexSampleRef] = []
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
            refs.append(DailyIndexSampleRef(part_index=int(part_index), origin_row=int(row)))
    return refs


def _batched_refs(refs: Sequence[DailyIndexSampleRef], batch_size: int) -> Iterator[list[DailyIndexSampleRef]]:
    size = max(1, int(batch_size))
    for start in range(0, len(refs), size):
        yield list(refs[start : start + size])


class _ReadyBatchBuffer:
    def __init__(self, *, batch_size: int, drop_last: bool) -> None:
        self.batch_size = max(1, int(batch_size))
        self.drop_last = bool(drop_last)
        self._chunks: list[DailyIndexTrainingBatch] = []
        self._samples = 0

    def add(self, batch: DailyIndexTrainingBatch) -> Iterator[DailyIndexTrainingBatch]:
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

    def flush(self) -> Iterator[DailyIndexTrainingBatch]:
        if self.drop_last or self._samples <= 0:
            self._chunks = []
            self._samples = 0
            return
        combined = _concat_training_batches(self._chunks)
        self._chunks = []
        self._samples = 0
        yield combined

    def telemetry(self) -> dict[str, int]:
        return {
            "ready_buffer_chunks": int(len(self._chunks)),
            "ready_buffer_samples": int(self._samples),
        }


@dataclass(slots=True)
class _RollingContextBatch:
    text_inputs: dict[str, dict[str, np.ndarray]] | None = None
    xbrl_inputs: dict[str, np.ndarray] | None = None
    corporate_action_inputs: dict[str, np.ndarray] | None = None
    bar_inputs: dict[str, dict[str, np.ndarray]] | None = None
    scanner_inputs: dict[str, np.ndarray] | None = None
    profile: dict[str, float | int] = field(default_factory=dict)


def _concat_training_batches(batches: Sequence[DailyIndexTrainingBatch]) -> DailyIndexTrainingBatch:
    nonempty = [batch for batch in batches if batch.sample_count > 0]
    if not nonempty:
        return _empty_batch("")
    first = nonempty[0]
    raw_windows = _concat_dict_arrays(batch.raw_event_windows for batch in nonempty)
    raw_flat = _concat_dict_arrays(batch.raw_event_flat for batch in nonempty)
    intraday_labels = _concat_dict_arrays(batch.intraday_labels for batch in nonempty)
    corporate_action_labels = _concat_dict_arrays(batch.corporate_action_labels for batch in nonempty)
    availability = _concat_dict_arrays(batch.input_availability for batch in nonempty)
    scanner_inputs = _concat_dict_arrays(batch.scanner_inputs for batch in nonempty)
    xbrl_inputs = _concat_dict_arrays(batch.xbrl_inputs for batch in nonempty)
    corporate_action_inputs = _concat_dict_arrays(batch.corporate_action_inputs for batch in nonempty)
    profile = {
        "samples": sum(int(batch.sample_count) for batch in nonempty),
    }
    for batch in nonempty:
        for key, value in batch.profile.items():
            if key == "samples":
                continue
            if isinstance(value, (int, float)):
                profile[key] = _merge_profile_metric(str(key), profile.get(key), value)
    return DailyIndexTrainingBatch(
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
        corporate_action_labels=corporate_action_labels,
        corporate_action_label_days=first.corporate_action_label_days,
        future_intraday_bar_horizons=first.future_intraday_bar_horizons,
        future_bar_values=_concat_dict_arrays(batch.future_bar_values for batch in nonempty),
        future_bar_masks=_concat_dict_arrays(batch.future_bar_masks for batch in nonempty),
        future_bar_feature_names=first.future_bar_feature_names,
        future_intraday_bars=_concat_optional_arrays([batch.future_intraday_bars for batch in nonempty]),
        future_intraday_bar_mask=_concat_optional_arrays([batch.future_intraday_bar_mask for batch in nonempty]),
        input_availability=availability,
        scanner_inputs=scanner_inputs,
        text_inputs=_concat_text_inputs(batch.text_inputs for batch in nonempty),
        xbrl_inputs=xbrl_inputs,
        corporate_action_inputs=corporate_action_inputs,
        bar_inputs=_concat_bar_inputs(nonempty),
        external_context=_merge_external_context(nonempty),
        profile=profile,
    )


def _slice_training_batch(batch: DailyIndexTrainingBatch, start: int, end: int) -> DailyIndexTrainingBatch:
    start = max(0, int(start))
    end = max(start, min(int(end), int(batch.sample_count)))
    profile = _slice_profile(batch.profile, int(batch.sample_count), end - start)
    return DailyIndexTrainingBatch(
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
        corporate_action_labels={key: value[start:end] for key, value in batch.corporate_action_labels.items()},
        corporate_action_label_days=batch.corporate_action_label_days,
        future_intraday_bar_horizons=batch.future_intraday_bar_horizons,
        future_bar_values={key: value[start:end] for key, value in batch.future_bar_values.items()},
        future_bar_masks={key: value[start:end] for key, value in batch.future_bar_masks.items()},
        future_bar_feature_names=batch.future_bar_feature_names,
        future_intraday_bars=batch.future_intraday_bars[start:end] if batch.future_intraday_bars.shape[0] else batch.future_intraday_bars,
        future_intraday_bar_mask=batch.future_intraday_bar_mask[start:end] if batch.future_intraday_bar_mask.shape[0] else batch.future_intraday_bar_mask,
        input_availability={key: value[start:end] for key, value in batch.input_availability.items()},
        scanner_inputs={key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in batch.scanner_inputs.items()},
        text_inputs={name: {key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in payload.items()} for name, payload in batch.text_inputs.items()},
        xbrl_inputs={key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in batch.xbrl_inputs.items()},
        corporate_action_inputs={key: (value if key in METADATA_PAYLOAD_FIELDS else _slice_batch_payload_field(value, start, end, int(batch.sample_count))) for key, value in batch.corporate_action_inputs.items()},
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


def _empty_scanner_payload(sample_count: int, config: DailyIndexLoaderConfig) -> dict[str, np.ndarray]:
    sample_count = max(0, int(sample_count))
    group_count = max(1, int(len(config.scanner_groups)))
    top_k = max(1, int(config.scanner_top_k))
    horizon_count = max(1, int(len(config.scanner_horizons)))
    family_count = int(len(BAR_FAMILY_KEYS))
    max_feature_width = int(max(len(BAR_FAMILY_FEATURE_KEYS[family]) for family in BAR_FAMILY_KEYS))
    time_width = int(len(BAR_TIME_FEATURE_COLUMNS))
    numeric_width = int(len(SCANNER_NUMERIC_FEATURE_KEYS))
    return {
        "leader_values": np.zeros((sample_count, group_count, top_k, horizon_count, family_count, max_feature_width), dtype=np.float32),
        "leader_mask": np.zeros((sample_count, group_count, top_k), dtype=np.bool_),
        "leader_horizon_mask": np.zeros((sample_count, group_count, top_k, horizon_count), dtype=np.bool_),
        "leader_time_features": np.zeros((sample_count, group_count, top_k, horizon_count, time_width), dtype=np.float32),
        "leader_start_time_features": np.zeros((sample_count, group_count, top_k, horizon_count, time_width), dtype=np.float32),
        "leader_end_time_features": np.zeros((sample_count, group_count, top_k, horizon_count, len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32),
        "leader_ticker_id": np.zeros((sample_count, group_count, top_k), dtype=np.int64),
        "leader_rank": np.zeros((sample_count, group_count, top_k), dtype=np.int64),
        "origin_values": np.zeros((sample_count, group_count, horizon_count, family_count, max_feature_width), dtype=np.float32),
        "origin_mask": np.zeros((sample_count, group_count), dtype=np.bool_),
        "origin_horizon_mask": np.zeros((sample_count, group_count, horizon_count), dtype=np.bool_),
        "origin_time_features": np.zeros((sample_count, group_count, horizon_count, time_width), dtype=np.float32),
        "origin_start_time_features": np.zeros((sample_count, group_count, horizon_count, time_width), dtype=np.float32),
        "origin_end_time_features": np.zeros((sample_count, group_count, horizon_count, len(BAR_END_FEATURE_COLUMNS)), dtype=np.float32),
        "origin_rank": np.zeros((sample_count, group_count), dtype=np.int64),
        "origin_in_topk": np.zeros((sample_count, group_count), dtype=np.bool_),
        "origin_topk_position": np.full((sample_count, group_count), -1, dtype=np.int64),
        "numeric_features": np.zeros((sample_count, group_count, numeric_width), dtype=np.float32),
        "group_names": np.asarray(tuple(config.scanner_groups), dtype=object),
        "horizons": np.asarray(tuple(config.scanner_horizons), dtype=object),
        "family_names": np.asarray(tuple(BAR_FAMILY_KEYS), dtype=object),
        "feature_names": np.asarray(tuple(BAR_FAMILY_FEATURE_KEYS["quote_bid"]), dtype=object),
        "numeric_feature_names": np.asarray(SCANNER_NUMERIC_FEATURE_KEYS, dtype=object),
        "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
        "start_time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
        "end_time_feature_names": np.asarray(BAR_END_FEATURE_COLUMNS, dtype=object),
    }


def _fill_scanner_values_from_artifact_row(
    values: np.ndarray,
    time_features: np.ndarray,
    start_time_features: np.ndarray,
    end_time_features: np.ndarray,
    *,
    index: ScannerArtifactIndex,
    artifact_row: int,
    horizons: Sequence[str],
    origin_timestamp_us: int,
) -> np.ndarray:
    row = int(artifact_row)
    horizon_mask = np.zeros((len(horizons),), dtype=np.bool_)
    for horizon_index, horizon in enumerate(horizons):
        horizon_token = _scanner_column_token(horizon)
        timestamp_col = f"{horizon_token}_timestamp_us"
        scanner_timestamp_us = int(index.timestamp_us[row])
        if timestamp_col in index.columns:
            scanner_timestamp_us = int(index.columns[timestamp_col][row])
        any_available = False
        for family_index, family in enumerate(BAR_FAMILY_KEYS):
            available_col = f"{family}_{horizon_token}_available"
            if available_col in index.columns and not bool(index.columns[available_col][row]):
                continue
            for feature_index, feature_name in enumerate(BAR_FAMILY_FEATURE_KEYS[family]):
                column = f"{family}_{horizon_token}_{feature_name}"
                if column in index.columns and feature_index < values.shape[-1]:
                    values[horizon_index, family_index, feature_index] = np.float32(index.columns[column][row])
                    any_available = True
        if any_available:
            horizon_mask[horizon_index] = True
            start_us, end_us = _scanner_horizon_start_end_us(index=index, row=row, horizon=horizon)
            if start_us <= 0 or end_us <= 0:
                end_us = int(scanner_timestamp_us)
                start_us = max(0, int(end_us) - int(_duration_us(horizon)))
            start_features = _bar_time_feature_matrix(np.asarray([start_us], dtype=np.int64), np.asarray([origin_timestamp_us], dtype=np.int64))[0]
            end_features = _bar_end_time_feature_matrix(np.asarray([end_us], dtype=np.int64), np.asarray([origin_timestamp_us], dtype=np.int64))[0]
            time_features[horizon_index] = start_features
            start_time_features[horizon_index] = start_features
            end_time_features[horizon_index] = end_features
    return horizon_mask


def _scanner_horizon_start_end_us(*, index: ScannerArtifactIndex, row: int, horizon: str) -> tuple[int, int]:
    try:
        source_date = str(index.source_date[int(row)])[:10]
        local_midnight_us = _ny_local_midnight_utc_us(source_date)
        scanner_resolution_values = index.columns.get("scanner_resolution_us")
        scanner_resolution_us = int(scanner_resolution_values[int(row)]) if scanner_resolution_values is not None and int(scanner_resolution_values.shape[0]) > int(row) else 1_000_000
        scanner_bucket = int(index.bucket[int(row)])
        horizon_us = _duration_us(str(horizon))
        resolution_us = _intraday_label_resolution_us(str(horizon), int(horizon_us))
        scanner_end_session_us = (scanner_bucket + 1) * max(1, int(scanner_resolution_us))
        join_bucket = max(int(SESSION_START_US // int(resolution_us)), int(scanner_end_session_us // int(resolution_us)) - 1)
        start_us = int(local_midnight_us) + int(join_bucket) * int(resolution_us)
        end_us = int(local_midnight_us) + int(join_bucket + 1) * int(resolution_us)
        return start_us, end_us
    except Exception:
        return 0, 0


def _ny_local_midnight_utc_us(source_date: str) -> int:
    local_date = dt.date.fromisoformat(str(source_date)[:10])
    local_midnight = dt.datetime.combine(local_date, dt.time(0, 0), tzinfo=ZoneInfo("America/New_York"))
    return int(local_midnight.astimezone(dt.timezone.utc).timestamp() * 1_000_000)


def _scanner_column_token(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _concat_bar_inputs(batches: Sequence[DailyIndexTrainingBatch]) -> dict[str, dict[str, np.ndarray]]:
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
            if field in {"values", "mask", "time_features"} or field.endswith("_values") or field.endswith("_mask") or field.endswith("_time_features"):
                payload[field] = np.concatenate(values, axis=0)
            else:
                payload[field] = values[0]
        out[name] = payload
    return out


def _slice_batch_payload_field(value: np.ndarray, start: int, end: int, sample_count: int) -> np.ndarray:
    if isinstance(value, np.ndarray) and value.shape[:1] and int(value.shape[0]) == int(sample_count):
        return value[start:end]
    return value


def _nested_array_nbytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, Mapping):
        return int(sum(_nested_array_nbytes(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return int(sum(_nested_array_nbytes(item) for item in value))
    return 0


def _snapshot_batch_row(value: Any, row: int, sample_count: int, field_name: str = "") -> Any:
    if field_name in METADATA_PAYLOAD_FIELDS:
        return value
    if isinstance(value, np.ndarray):
        if value.dtype != object and value.shape[:1] and int(value.shape[0]) == int(sample_count):
            return value[int(row)].copy()
        return value
    if isinstance(value, Mapping):
        return {key: _snapshot_batch_row(item, row, sample_count, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_snapshot_batch_row(item, row, sample_count, field_name) for item in value]
    if isinstance(value, tuple):
        return tuple(_snapshot_batch_row(item, row, sample_count, field_name) for item in value)
    return value


def _empty_batch_payload_like(value: Any, sample_count: int, field_name: str = "") -> Any:
    sample_count = max(0, int(sample_count))
    if field_name in METADATA_PAYLOAD_FIELDS:
        return value
    if isinstance(value, np.ndarray):
        if value.dtype != object:
            return np.zeros((sample_count, *value.shape), dtype=value.dtype)
        return value
    if isinstance(value, Mapping):
        return {key: _empty_batch_payload_like(item, sample_count, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_empty_batch_payload_like(item, sample_count, field_name) for item in value]
    if isinstance(value, tuple):
        return tuple(_empty_batch_payload_like(item, sample_count, field_name) for item in value)
    return value


def _empty_batch_payload_from_batch(value: Any, sample_count: int, source_sample_count: int, field_name: str = "") -> Any:
    sample_count = max(0, int(sample_count))
    source_sample_count = max(0, int(source_sample_count))
    if field_name in METADATA_PAYLOAD_FIELDS:
        return value
    if isinstance(value, np.ndarray):
        if value.dtype != object and value.shape[:1] and int(value.shape[0]) == int(source_sample_count):
            return np.zeros((sample_count, *value.shape[1:]), dtype=value.dtype)
        return value
    if isinstance(value, Mapping):
        return {
            key: _empty_batch_payload_from_batch(item, sample_count, source_sample_count, str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_empty_batch_payload_from_batch(item, sample_count, source_sample_count, field_name) for item in value]
    if isinstance(value, tuple):
        return tuple(_empty_batch_payload_from_batch(item, sample_count, source_sample_count, field_name) for item in value)
    return value


def _restore_batch_row(target: Any, snapshot: Any, row: int, sample_count: int, field_name: str = "") -> None:
    if field_name in METADATA_PAYLOAD_FIELDS:
        return
    if isinstance(target, np.ndarray):
        if target.dtype != object and target.shape[:1] and int(target.shape[0]) == int(sample_count):
            target[int(row)] = snapshot
        return
    if isinstance(target, Mapping) and isinstance(snapshot, Mapping):
        for key, item in target.items():
            if key in snapshot:
                _restore_batch_row(item, snapshot[key], row, sample_count, str(key))
        return
    if isinstance(target, list) and isinstance(snapshot, list):
        for item, saved in zip(target, snapshot):
            _restore_batch_row(item, saved, row, sample_count, field_name)


def _copy_batch_row(target: Any, source: Any, *, target_row: int, source_row: int, source_sample_count: int) -> None:
    snapshot = _snapshot_batch_row(source, int(source_row), int(source_sample_count))
    target_sample_count = _payload_sample_count(target)
    if target_sample_count <= 0:
        return
    if isinstance(target, np.ndarray):
        if target.dtype != object and target.shape[:1]:
            target[int(target_row)] = snapshot
        return
    if isinstance(target, Mapping) and isinstance(snapshot, Mapping):
        for key, item in target.items():
            if key in snapshot and key not in METADATA_PAYLOAD_FIELDS:
                _restore_batch_row(item, snapshot[key], int(target_row), target_sample_count)
        return
    _restore_batch_row(target, snapshot, int(target_row), target_sample_count)


def _payload_sample_count(value: Any) -> int:
    if isinstance(value, np.ndarray) and value.dtype != object and value.shape[:1]:
        return int(value.shape[0])
    if isinstance(value, Mapping):
        for item in value.values():
            count = _payload_sample_count(item)
            if count > 0:
                return int(count)
    if isinstance(value, (list, tuple)):
        for item in value:
            count = _payload_sample_count(item)
            if count > 0:
                return int(count)
    return 0


def _context_source_timestamps_us(frame: Any, source_name: str) -> np.ndarray:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return np.zeros((0,), dtype=np.int64)
    columns = set(getattr(frame, "columns", ()))
    candidates_by_source = {
        "ticker_news_embeddings": ("published_timestamp_us", "timestamp_us", "available_timestamp_us"),
        "market_news_embeddings": ("published_timestamp_us", "timestamp_us", "available_timestamp_us"),
        "sec_filing_embeddings": ("accepted_timestamp_us", "timestamp_us", "available_timestamp_us"),
        "xbrl": ("timestamp_us", "available_timestamp_us", "accepted_timestamp_us"),
        "corporate_actions": ("available_timestamp_us", "timestamp_us", "effective_timestamp_us"),
    }
    candidates = candidates_by_source.get(str(source_name), ("timestamp_us", "available_timestamp_us"))
    for column in candidates:
        if column in columns:
            values = frame.get_column(column).to_numpy()
            try:
                out = np.asarray(values, dtype=np.int64)
            except (TypeError, ValueError):
                continue
            out = out[out > 0]
            if out.size:
                return np.sort(out)
    return np.zeros((0,), dtype=np.int64)


def _strip_internal_cache_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _strip_internal_cache_fields(item)
            for key, item in value.items()
            if not str(key).startswith("__cache_")
        }
    if isinstance(value, list):
        return [_strip_internal_cache_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_internal_cache_fields(item) for item in value)
    return value


def _daily_bar_cache_signature(
    materializer: DailyIndexBatchMaterializer,
    part: LoadedDailyIndexPart,
    origin_timestamp_us: int,
    payload_name: str,
    config: DailyIndexLoaderConfig,
) -> tuple[Any, ...]:
    group = "global_daily_bars" if str(payload_name) == "global_daily_bars" else "daily_bars"
    frame = part.context.get(group)
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return (str(payload_name), "empty")
    index = materializer._daily_bar_context_index(frame)
    offsets = np.asarray(_bar_offsets_for_group(config, group), dtype=np.int64)
    cutoff_ms = np.asarray([int(origin_timestamp_us) // 1000 - int(max(0.0, float(config.daily_bar_completion_lag_hours)) * 3_600_000.0)], dtype=np.int64)
    symbols = tuple(index.symbols) if str(payload_name) == "global_daily_bars" else (str(part.plan.ticker),)
    signature: list[Any] = [str(payload_name)]
    for symbol in symbols:
        signature.append(str(symbol))
        for family in BAR_FAMILY_KEYS:
            starts = index.bar_start_ms_by_family_symbol.get(family, {}).get(str(symbol))
            if starts is None or starts.size <= 0:
                signature.extend((family, tuple(-1 for _ in offsets)))
                continue
            selected, selected_mask = _select_completed_bar_rows(starts, cutoff_ms, offsets)
            values = np.where(selected_mask[0], starts[selected[0]], -1).astype(np.int64, copy=False)
            signature.extend((family, tuple(int(value) for value in values)))
    return tuple(signature)


def _daily_bar_cache_timestamp_arrays(
    materializer: DailyIndexBatchMaterializer,
    part: LoadedDailyIndexPart,
    origin_timestamp_us: int,
    payload_name: str,
    config: DailyIndexLoaderConfig,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    group = "global_daily_bars" if str(payload_name) == "global_daily_bars" else "daily_bars"
    frame = part.context.get(group)
    offsets = np.asarray(_bar_offsets_for_group(config, group), dtype=np.int64)
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        shape = (0, int(offsets.shape[0])) if str(payload_name) == "global_daily_bars" else (int(offsets.shape[0]),)
        return {family: (np.zeros(shape, dtype=np.int64), np.zeros(shape, dtype=np.int64)) for family in BAR_FAMILY_KEYS}
    index = materializer._daily_bar_context_index(frame)
    symbols = tuple(index.symbols) if str(payload_name) == "global_daily_bars" else (str(part.plan.ticker),)
    cutoff_ms = np.asarray([int(origin_timestamp_us) // 1000 - int(max(0.0, float(config.daily_bar_completion_lag_hours)) * 3_600_000.0)], dtype=np.int64)
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for family in BAR_FAMILY_KEYS:
        starts_shape = (len(symbols), int(offsets.shape[0])) if str(payload_name) == "global_daily_bars" else (int(offsets.shape[0]),)
        starts_us = np.zeros(starts_shape, dtype=np.int64)
        ends_us = np.zeros(starts_shape, dtype=np.int64)
        for symbol_index, symbol in enumerate(symbols):
            starts = index.bar_start_ms_by_family_symbol.get(family, {}).get(str(symbol))
            if starts is None or starts.size <= 0:
                continue
            selected, selected_mask = _select_completed_bar_rows(starts, cutoff_ms, offsets)
            selected_starts = np.where(selected_mask[0], starts[selected[0]], 0).astype(np.int64, copy=False)
            selected_ends = np.where(selected_mask[0], selected_starts + 86_400_000, 0).astype(np.int64, copy=False)
            if str(payload_name) == "global_daily_bars":
                starts_us[symbol_index] = selected_starts * 1000
                ends_us[symbol_index] = selected_ends * 1000
            else:
                starts_us[:] = selected_starts * 1000
                ends_us[:] = selected_ends * 1000
        out[family] = (starts_us, ends_us)
    return out


def _intraday_bar_cache_signature(part: LoadedDailyIndexPart, origin_row: int, config: DailyIndexLoaderConfig) -> tuple[Any, ...]:
    timestamps = _intraday_bar_cache_timestamp_arrays(part, origin_row, config)
    start_ts, end_ts = timestamps
    return ("ticker_intraday_bars", tuple(int(value) for value in start_ts.reshape(-1)), tuple(int(value) for value in end_ts.reshape(-1)))


def _intraday_bar_cache_timestamp_arrays(part: LoadedDailyIndexPart, origin_row: int, config: DailyIndexLoaderConfig) -> tuple[np.ndarray, np.ndarray]:
    horizons = _cached_intraday_context_horizons([part], config=config)
    specs = _intraday_horizon_specs(horizons)
    horizon_count = int(len(specs))
    start_ts = np.zeros((horizon_count,), dtype=np.int64)
    end_ts = np.zeros((horizon_count,), dtype=np.int64)
    if horizon_count <= 0 or part.origins is None:
        return start_ts, end_ts
    if "origin_local_session_us" not in part.origins.columns:
        return start_ts, end_ts
    origin_ts = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[int(origin_row)])
    local_session_us = int(part.origin_array("origin_local_session_us").astype(np.int64, copy=False)[int(origin_row)])
    local_midnight_us = origin_ts - local_session_us
    for horizon_index, (_horizon, _horizon_us, resolution_us, bucket_count, is_eod) in enumerate(specs):
        origin_bucket = local_session_us // int(resolution_us)
        last_bucket = origin_bucket - 1
        first_bucket = 0 if is_eod else max(0, int(last_bucket) - int(bucket_count) + 1)
        if int(last_bucket) < int(first_bucket):
            continue
        start_ts[horizon_index] = int(local_midnight_us) + int(first_bucket) * int(resolution_us)
        end_ts[horizon_index] = int(local_midnight_us) + int(last_bucket + 1) * int(resolution_us)
    return start_ts, end_ts


def _scanner_cache_signature(
    materializer: DailyIndexBatchMaterializer,
    part: LoadedDailyIndexPart,
    origin_row: int,
    config: DailyIndexLoaderConfig,
) -> tuple[Any, ...]:
    frame = part.context.get("scanner_context")
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return ("scanner_context", str(part.plan.ticker), "empty")
    path = part.context_paths.get("scanner_context")
    index = materializer._scanner_artifact_index(frame, cache_key=str(path.resolve()) if path else f"frame:{id(frame)}")
    origin_date = str(part.origins.get_column("origin_local_date")[origin_row])[:10] if part.origins is not None and "origin_local_date" in part.origins.columns else str(part.plan.source_date)[:10]
    origin_session_us = int(part.origin_array("origin_local_session_us")[origin_row]) if part.origins is not None and "origin_local_session_us" in part.origins.columns else 0
    scanner_resolution_us = int(index.columns.get("scanner_resolution_us", np.asarray([1_000_000], dtype=np.int64))[0]) if index.columns else 1_000_000
    bucket = int(max(0, origin_session_us - 1) // max(1, scanner_resolution_us))
    return ("scanner_context", origin_date, bucket, str(part.plan.ticker))


def _annotate_bar_cache_payloads(
    materializer: DailyIndexBatchMaterializer,
    payloads: dict[str, Any],
    parts: Sequence[LoadedDailyIndexPart],
    refs: Sequence[DailyIndexSampleRef],
    config: DailyIndexLoaderConfig,
) -> None:
    if not refs:
        return
    origin_timestamps = _identity_arrays(parts, refs)[2]
    for payload_name, payload in list(payloads.items()):
        if not isinstance(payload, dict) or not str(payload_name).endswith("_bars"):
            continue
        sample_count = int(len(refs))
        if str(payload_name) == "ticker_intraday_bars":
            mask = payload.get("mask")
            if not isinstance(mask, np.ndarray) or not mask.shape[:1]:
                continue
            starts = np.zeros(mask.shape, dtype=np.int64)
            ends = np.zeros(mask.shape, dtype=np.int64)
            for row, ref in enumerate(refs):
                part = parts[int(ref.part_index)]
                start_ts, end_ts = _intraday_bar_cache_timestamp_arrays(part, int(ref.origin_row), config)
                width = min(int(starts.shape[1]), int(start_ts.shape[0]))
                if width:
                    starts[row, :width] = start_ts[:width]
                    ends[row, :width] = end_ts[:width]
            payload["__cache_start_timestamp_us"] = starts
            payload["__cache_end_timestamp_us"] = ends
            for family in BAR_FAMILY_KEYS:
                payload[f"__cache_{family}_start_timestamp_us"] = starts.copy()
                payload[f"__cache_{family}_end_timestamp_us"] = ends.copy()
            continue
        mask = payload.get("mask")
        if not isinstance(mask, np.ndarray) or not mask.shape[:1]:
            continue
        shape = mask.shape
        family_starts = {family: np.zeros(shape, dtype=np.int64) for family in BAR_FAMILY_KEYS}
        family_ends = {family: np.zeros(shape, dtype=np.int64) for family in BAR_FAMILY_KEYS}
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            arrays = _daily_bar_cache_timestamp_arrays(materializer, part, int(origin_timestamps[row]), str(payload_name), config)
            for family, (starts, ends) in arrays.items():
                if str(payload_name) == "global_daily_bars":
                    if starts.shape == shape[1:]:
                        family_starts[family][row] = starts
                        family_ends[family][row] = ends
                else:
                    width = min(int(shape[1]), int(starts.shape[0]))
                    if width:
                        family_starts[family][row, :width] = starts[:width]
                        family_ends[family][row, :width] = ends[:width]
        for family in BAR_FAMILY_KEYS:
            payload[f"__cache_{family}_start_timestamp_us"] = family_starts[family]
            payload[f"__cache_{family}_end_timestamp_us"] = family_ends[family]
        payload["__cache_start_timestamp_us"] = family_starts.get("trade", np.zeros(shape, dtype=np.int64))
        payload["__cache_end_timestamp_us"] = family_ends.get("trade", np.zeros(shape, dtype=np.int64))


def _annotate_scanner_cache_payload(
    materializer: DailyIndexBatchMaterializer,
    payloads: dict[str, Any],
    parts: Sequence[LoadedDailyIndexPart],
    refs: Sequence[DailyIndexSampleRef],
    config: DailyIndexLoaderConfig,
) -> None:
    payload = payloads.get("scanner_context")
    if not isinstance(payload, dict) or not refs:
        return
    leader_mask = payload.get("leader_horizon_mask")
    origin_mask = payload.get("origin_horizon_mask")
    if not isinstance(leader_mask, np.ndarray) or not isinstance(origin_mask, np.ndarray):
        return
    leader_start = np.zeros(leader_mask.shape, dtype=np.int64)
    leader_end = np.zeros(leader_mask.shape, dtype=np.int64)
    origin_start = np.zeros(origin_mask.shape, dtype=np.int64)
    origin_end = np.zeros(origin_mask.shape, dtype=np.int64)
    group_names = tuple(str(group) for group in config.scanner_groups)
    horizons = tuple(str(horizon) for horizon in config.scanner_horizons)
    top_k = max(1, int(config.scanner_top_k))
    for row, ref in enumerate(refs):
        part = parts[int(ref.part_index)]
        frame = part.context.get("scanner_context")
        if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
            continue
        path = part.context_paths.get("scanner_context")
        index = materializer._scanner_artifact_index(frame, cache_key=str(path.resolve()) if path else f"frame:{id(frame)}")
        if index.bucket.shape[0] <= 0:
            continue
        origin_row = int(ref.origin_row)
        origin_date = str(part.origins.get_column("origin_local_date")[origin_row])[:10] if part.origins is not None and "origin_local_date" in part.origins.columns else str(part.plan.source_date)[:10]
        origin_session_us = int(part.origin_array("origin_local_session_us")[origin_row]) if part.origins is not None and "origin_local_session_us" in part.origins.columns else 0
        scanner_resolution_us = int(index.columns.get("scanner_resolution_us", np.asarray([1_000_000], dtype=np.int64))[0])
        bucket = int(max(0, origin_session_us - 1) // max(1, scanner_resolution_us))
        origin_ticker = str(part.plan.ticker)
        origin_artifact_row = index.row_by_key.get((origin_date, bucket, origin_ticker))
        for group_index, group_name in enumerate(group_names):
            leader_rows = index.leaders_by_key.get((origin_date, bucket, group_name))
            if leader_rows is not None:
                for leader_position, artifact_row in enumerate(leader_rows[:top_k]):
                    artifact_row = int(artifact_row)
                    for horizon_index, horizon in enumerate(horizons):
                        start_us, end_us = _scanner_horizon_start_end_us(index=index, row=artifact_row, horizon=horizon)
                        leader_start[row, group_index, leader_position, horizon_index] = int(start_us)
                        leader_end[row, group_index, leader_position, horizon_index] = int(end_us)
            if origin_artifact_row is not None:
                for horizon_index, horizon in enumerate(horizons):
                    start_us, end_us = _scanner_horizon_start_end_us(index=index, row=int(origin_artifact_row), horizon=horizon)
                    origin_start[row, group_index, horizon_index] = int(start_us)
                    origin_end[row, group_index, horizon_index] = int(end_us)
    payload["__cache_leader_start_timestamp_us"] = leader_start
    payload["__cache_leader_end_timestamp_us"] = leader_end
    payload["__cache_origin_start_timestamp_us"] = origin_start
    payload["__cache_origin_end_timestamp_us"] = origin_end


def _refresh_text_payload_time_features(payload: Mapping[str, Any], row: int, origin_timestamp_us: int) -> None:
    timestamps = payload.get("item_timestamp_us")
    features = payload.get("item_time_features")
    if not isinstance(timestamps, np.ndarray) or not isinstance(features, np.ndarray):
        return
    if not timestamps.shape[:1] or int(row) >= int(timestamps.shape[0]):
        return
    selected = timestamps[int(row)].astype(np.int64, copy=False)
    origins = np.full(selected.shape, int(origin_timestamp_us), dtype=np.int64)
    relative = _relative_time_feature_matrix(selected, origins)
    if features.shape[-1] >= int(relative.shape[-1]):
        features[int(row), ..., -int(relative.shape[-1]) :] = relative


def _refresh_xbrl_payload_time_features(payload: Mapping[str, Any], row: int, origin_timestamp_us: int) -> None:
    timestamps = payload.get("timestamp_us")
    mask = payload.get("mask")
    if not isinstance(timestamps, np.ndarray) or not isinstance(mask, np.ndarray):
        return
    selected = timestamps[int(row)].astype(np.int64, copy=False)
    selected_mask = mask[int(row)].astype(np.bool_, copy=False)
    origins = np.full(selected.shape, int(origin_timestamp_us), dtype=np.int64)
    timestamp_features = _timestamp_feature_arrays(selected, origins)
    for key, values in timestamp_features.items():
        target = payload.get(key)
        if isinstance(target, np.ndarray) and target.shape[:1]:
            target[int(row)] = values.astype(target.dtype, copy=False)
    time_features = payload.get("time_features")
    if isinstance(time_features, np.ndarray) and time_features.shape[:1]:
        absolute = time_features[int(row), :, : len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)]
        relative = _relative_time_feature_matrix(selected, origins)
        refreshed = np.concatenate([absolute, relative], axis=-1).astype(np.float32, copy=False)
        refreshed[~selected_mask] = 0.0
        time_features[int(row)] = refreshed
    age_days = payload.get("age_days")
    if isinstance(age_days, np.ndarray) and age_days.shape[:1]:
        age_days[int(row)] = (
            np.maximum(0.0, (float(origin_timestamp_us) - selected.astype(np.float64)) / 86_400_000_000.0).astype(np.float32)
            * selected_mask
        )
    period_days = payload.get("period_end_days")
    period_features = payload.get("period_end_time_features")
    if isinstance(period_days, np.ndarray) and isinstance(period_features, np.ndarray) and period_features.shape[:1]:
        absolute_width = len(XBRL_PERIOD_END_TIME_FEATURE_COLUMNS) - 2
        absolute = period_features[int(row), :, :absolute_width]
        origin_days = float(origin_timestamp_us) / 86_400_000_000.0
        period_age_days = np.maximum(0.0, origin_days - period_days[int(row)].astype(np.float64)).astype(np.float32)
        relative = np.stack([period_age_days, np.log1p(period_age_days.astype(np.float64)).astype(np.float32)], axis=-1)
        refreshed = np.concatenate([absolute, relative], axis=-1).astype(np.float32, copy=False)
        refreshed[~selected_mask] = 0.0
        period_features[int(row)] = refreshed


def _refresh_corporate_action_payload_time_features(payload: Mapping[str, Any], row: int, origin_timestamp_us: int) -> None:
    mask = payload.get("mask")
    available = payload.get("available_timestamp_us")
    effective = payload.get("effective_timestamp_us")
    if not isinstance(mask, np.ndarray) or not isinstance(available, np.ndarray) or not isinstance(effective, np.ndarray):
        return
    selected_mask = mask[int(row)].astype(np.bool_, copy=False)
    origins = np.full(available[int(row)].shape, int(origin_timestamp_us), dtype=np.int64)
    time_features = payload.get("time_features")
    if isinstance(time_features, np.ndarray) and time_features.shape[:1]:
        absolute = time_features[int(row), :, : len(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)]
        refreshed = np.concatenate([absolute, _relative_time_feature_matrix(available[int(row)], origins)], axis=-1).astype(np.float32, copy=False)
        refreshed[~selected_mask] = 0.0
        time_features[int(row)] = refreshed
    effective_time_features = payload.get("effective_time_features")
    if isinstance(effective_time_features, np.ndarray) and effective_time_features.shape[:1]:
        absolute = effective_time_features[int(row), :, : len(CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS)]
        refreshed = np.concatenate([absolute, _relative_time_feature_matrix(effective[int(row)], origins)], axis=-1).astype(np.float32, copy=False)
        refreshed[~selected_mask] = 0.0
        effective_time_features[int(row)] = refreshed


def _refresh_bar_payload_time_features(payload: Mapping[str, Any], row: int, origin_timestamp_us: int) -> None:
    origin_ts = int(origin_timestamp_us)
    for family in BAR_FAMILY_KEYS:
        start_ts = payload.get(f"__cache_{family}_start_timestamp_us")
        end_ts = payload.get(f"__cache_{family}_end_timestamp_us")
        family_mask = payload.get(f"{family}_mask")
        if not isinstance(start_ts, np.ndarray) or not isinstance(end_ts, np.ndarray):
            continue
        if int(row) >= int(start_ts.shape[0]):
            continue
        starts = start_ts[int(row)].astype(np.int64, copy=False)
        ends = end_ts[int(row)].astype(np.int64, copy=False)
        origins = np.full(starts.shape, origin_ts, dtype=np.int64)
        start_features = _bar_time_feature_matrix(starts, origins)
        end_features = _bar_end_time_feature_matrix(ends, origins)
        if isinstance(family_mask, np.ndarray) and family_mask.shape[:1] and int(row) < int(family_mask.shape[0]):
            valid = family_mask[int(row)].astype(np.bool_, copy=False)
            start_features[~valid] = 0.0
            end_features[~valid] = 0.0
        for key in (f"{family}_time_features", f"{family}_start_time_features"):
            target = payload.get(key)
            if isinstance(target, np.ndarray) and target.shape[:1] and int(row) < int(target.shape[0]):
                target[int(row)] = start_features
        target_end = payload.get(f"{family}_end_time_features")
        if isinstance(target_end, np.ndarray) and target_end.shape[:1] and int(row) < int(target_end.shape[0]):
            target_end[int(row)] = end_features
    start_ts = payload.get("__cache_start_timestamp_us")
    end_ts = payload.get("__cache_end_timestamp_us")
    mask = payload.get("mask")
    if isinstance(start_ts, np.ndarray) and isinstance(end_ts, np.ndarray) and start_ts.shape[:1] and int(row) < int(start_ts.shape[0]):
        starts = start_ts[int(row)].astype(np.int64, copy=False)
        ends = end_ts[int(row)].astype(np.int64, copy=False)
        origins = np.full(starts.shape, origin_ts, dtype=np.int64)
        start_features = _bar_time_feature_matrix(starts, origins)
        end_features = _bar_end_time_feature_matrix(ends, origins)
        if isinstance(mask, np.ndarray) and mask.shape[:1] and int(row) < int(mask.shape[0]):
            valid = mask[int(row)].astype(np.bool_, copy=False)
            start_features[~valid] = 0.0
            end_features[~valid] = 0.0
        for key in ("time_features", "start_time_features"):
            target = payload.get(key)
            if isinstance(target, np.ndarray) and target.shape[:1] and int(row) < int(target.shape[0]):
                target[int(row)] = start_features
        target_end = payload.get("end_time_features")
        if isinstance(target_end, np.ndarray) and target_end.shape[:1] and int(row) < int(target_end.shape[0]):
            target_end[int(row)] = end_features


def _refresh_scanner_payload_time_features(payload: Mapping[str, Any], row: int, origin_timestamp_us: int) -> None:
    origin_ts = int(origin_timestamp_us)
    leader_start = payload.get("__cache_leader_start_timestamp_us")
    leader_end = payload.get("__cache_leader_end_timestamp_us")
    leader_mask = payload.get("leader_horizon_mask")
    if isinstance(leader_start, np.ndarray) and isinstance(leader_end, np.ndarray) and leader_start.shape[:1] and int(row) < int(leader_start.shape[0]):
        starts = leader_start[int(row)].astype(np.int64, copy=False)
        ends = leader_end[int(row)].astype(np.int64, copy=False)
        origins = np.full(starts.shape, origin_ts, dtype=np.int64)
        start_features = _bar_time_feature_matrix(starts, origins)
        end_features = _bar_end_time_feature_matrix(ends, origins)
        if isinstance(leader_mask, np.ndarray) and leader_mask.shape[:1] and int(row) < int(leader_mask.shape[0]):
            valid = leader_mask[int(row)].astype(np.bool_, copy=False)
            start_features[~valid] = 0.0
            end_features[~valid] = 0.0
        for key in ("leader_time_features", "leader_start_time_features"):
            target = payload.get(key)
            if isinstance(target, np.ndarray) and target.shape[:1]:
                target[int(row)] = start_features
        target_end = payload.get("leader_end_time_features")
        if isinstance(target_end, np.ndarray) and target_end.shape[:1]:
            target_end[int(row)] = end_features
    origin_start = payload.get("__cache_origin_start_timestamp_us")
    origin_end = payload.get("__cache_origin_end_timestamp_us")
    origin_mask = payload.get("origin_horizon_mask")
    if isinstance(origin_start, np.ndarray) and isinstance(origin_end, np.ndarray) and origin_start.shape[:1] and int(row) < int(origin_start.shape[0]):
        starts = origin_start[int(row)].astype(np.int64, copy=False)
        ends = origin_end[int(row)].astype(np.int64, copy=False)
        origins = np.full(starts.shape, origin_ts, dtype=np.int64)
        start_features = _bar_time_feature_matrix(starts, origins)
        end_features = _bar_end_time_feature_matrix(ends, origins)
        if isinstance(origin_mask, np.ndarray) and origin_mask.shape[:1] and int(row) < int(origin_mask.shape[0]):
            valid = origin_mask[int(row)].astype(np.bool_, copy=False)
            start_features[~valid] = 0.0
            end_features[~valid] = 0.0
        for key in ("origin_time_features", "origin_start_time_features"):
            target = payload.get(key)
            if isinstance(target, np.ndarray) and target.shape[:1]:
                target[int(row)] = start_features
        target_end = payload.get("origin_end_time_features")
        if isinstance(target_end, np.ndarray) and target_end.shape[:1]:
            target_end[int(row)] = end_features


def _slice_profile(profile: Mapping[str, float | int], source_samples: int, output_samples: int) -> dict[str, float | int]:
    out: dict[str, float | int] = {"samples": int(output_samples)}
    if int(source_samples) <= 0:
        return out
    scale = float(output_samples) / float(source_samples)
    for key, value in profile.items():
        if key == "samples":
            continue
        if isinstance(value, (int, float)):
            out[key] = float(value) * scale if _profile_metric_should_scale(str(key)) else value
    return out


def _merge_profile_metric(key: str, current: Any, value: float | int) -> float | int:
    if current is None:
        return value
    if _profile_metric_should_scale(key):
        return float(current) + float(value)
    if key.endswith("_start_timestamp_us") or key.endswith("/start_timestamp_us") or "window_start_timestamp_us" in key:
        return min(int(current), int(value))
    if key.endswith("_end_timestamp_us") or key.endswith("/end_timestamp_us") or "window_end_timestamp_us" in key:
        return max(int(current), int(value))
    return value


def _profile_metric_should_scale(key: str) -> bool:
    key = str(key)
    if key.startswith("cache_first_") or key.startswith("event_cache_warm_") or key.startswith("context_cache_warm_"):
        return False
    if key in {"origin_cursor_initial_seconds", "cache_first_cursor_build_seconds"}:
        return False
    return (
        key.endswith("_seconds")
        or key.endswith("/seconds")
        or key in {"materialize_wait_seconds", "materialize_seconds", "event_seconds", "label_seconds", "text_seconds", "xbrl_seconds"}
    )


def _concat_optional_arrays(items: Sequence[np.ndarray]) -> np.ndarray:
    arrays = [item for item in items if getattr(item, "shape", (0,))[0] > 0]
    if not arrays:
        return items[0] if items else np.asarray([])
    return np.concatenate(arrays, axis=0)


def _validate_materialized_time_feature_contract(
    *,
    raw_stream: np.ndarray,
    raw_stream_feature_names: Sequence[str],
    text_inputs: Mapping[str, Mapping[str, Any]],
    xbrl_inputs: Mapping[str, Any],
    corporate_action_inputs: Mapping[str, Any],
    bar_inputs: Mapping[str, Mapping[str, Any]],
    scanner_inputs: Mapping[str, Any],
) -> None:
    if getattr(raw_stream, "size", 0):
        names = tuple(str(name) for name in raw_stream_feature_names)
        if int(raw_stream.shape[-1]) != len(names):
            raise RuntimeError(f"Raw event stream width {int(raw_stream.shape[-1])} does not match feature-name count {len(names)}.")
        missing = [name for name in EVENT_TIME_FEATURE_COLUMNS if name not in names]
        if missing:
            raise RuntimeError(f"Raw event stream is missing event time features: {', '.join(missing)}")

    for group, payload in text_inputs.items():
        if _payload_array_has_rows(payload, "embeddings"):
            _assert_payload_time_array(payload, "item_time_features", len(TEXT_ITEM_TIME_FEATURE_COLUMNS), f"text_inputs.{group}.item_time_features")
            names = tuple(str(name) for name in payload.get("item_time_feature_names", ()))
            if names and names != TEXT_ITEM_TIME_FEATURE_COLUMNS:
                raise RuntimeError(f"text_inputs.{group}.item_time_feature_names does not match the loader time-feature contract.")

    if _payload_array_has_rows(xbrl_inputs, "value"):
        _assert_payload_time_array(xbrl_inputs, "time_features", len(XBRL_TIME_FEATURE_COLUMNS), "xbrl_inputs.time_features")
        _assert_payload_time_array(xbrl_inputs, "period_end_time_features", len(XBRL_PERIOD_END_TIME_FEATURE_COLUMNS), "xbrl_inputs.period_end_time_features")
        names = tuple(str(name) for name in xbrl_inputs.get("time_feature_names", ()))
        if names and names != XBRL_TIME_FEATURE_COLUMNS:
            raise RuntimeError("xbrl_inputs.time_feature_names does not match the loader time-feature contract.")
        period_names = tuple(str(name) for name in xbrl_inputs.get("period_end_time_feature_names", ()))
        if period_names and period_names != XBRL_PERIOD_END_TIME_FEATURE_COLUMNS:
            raise RuntimeError("xbrl_inputs.period_end_time_feature_names does not match the loader time-feature contract.")

    if _payload_array_has_rows(corporate_action_inputs, "numeric_features"):
        _assert_payload_time_array(corporate_action_inputs, "time_features", len(CORPORATE_ACTION_TIME_FEATURE_COLUMNS), "corporate_action_inputs.time_features")
        _assert_payload_time_array(
            corporate_action_inputs,
            "effective_time_features",
            len(CORPORATE_ACTION_EFFECTIVE_TIME_FEATURE_COLUMNS),
            "corporate_action_inputs.effective_time_features",
        )
        names = tuple(str(name) for name in corporate_action_inputs.get("time_feature_names", ()))
        if names and names != CORPORATE_ACTION_TIME_FEATURE_COLUMNS:
            raise RuntimeError("corporate_action_inputs.time_feature_names does not match the loader time-feature contract.")
        effective_names = tuple(str(name) for name in corporate_action_inputs.get("effective_time_feature_names", ()))
        if effective_names and effective_names != CORPORATE_ACTION_EFFECTIVE_TIME_FEATURE_COLUMNS:
            raise RuntimeError("corporate_action_inputs.effective_time_feature_names does not match the loader time-feature contract.")

    for group, payload in bar_inputs.items():
        if _payload_array_has_rows(payload, "values"):
            _assert_payload_time_array(payload, "time_features", len(BAR_TIME_FEATURE_COLUMNS), f"bar_inputs.{group}.time_features")
            _assert_payload_time_array(payload, "start_time_features", len(BAR_TIME_FEATURE_COLUMNS), f"bar_inputs.{group}.start_time_features")
            _assert_payload_time_array(payload, "end_time_features", len(BAR_END_FEATURE_COLUMNS), f"bar_inputs.{group}.end_time_features")
        names = tuple(str(name) for name in payload.get("time_feature_names", ()))
        if names and names != BAR_TIME_FEATURE_COLUMNS:
            raise RuntimeError(f"bar_inputs.{group}.time_feature_names does not match the loader time-feature contract.")
        start_names = tuple(str(name) for name in payload.get("start_time_feature_names", ()))
        if start_names and start_names != BAR_TIME_FEATURE_COLUMNS:
            raise RuntimeError(f"bar_inputs.{group}.start_time_feature_names does not match the loader time-feature contract.")
        end_names = tuple(str(name) for name in payload.get("end_time_feature_names", ()))
        if end_names and end_names != BAR_END_FEATURE_COLUMNS:
            raise RuntimeError(f"bar_inputs.{group}.end_time_feature_names does not match the loader time-feature contract.")
        for family in BAR_FAMILY_KEYS:
            if _payload_array_has_rows(payload, f"{family}_values"):
                _assert_payload_time_array(payload, f"{family}_time_features", len(BAR_TIME_FEATURE_COLUMNS), f"bar_inputs.{group}.{family}_time_features")
                _assert_payload_time_array(payload, f"{family}_start_time_features", len(BAR_TIME_FEATURE_COLUMNS), f"bar_inputs.{group}.{family}_start_time_features")
                _assert_payload_time_array(payload, f"{family}_end_time_features", len(BAR_END_FEATURE_COLUMNS), f"bar_inputs.{group}.{family}_end_time_features")

    if _payload_array_has_rows(scanner_inputs, "leader_values"):
        _assert_payload_time_array(scanner_inputs, "leader_time_features", len(BAR_TIME_FEATURE_COLUMNS), "scanner_inputs.leader_time_features")
        _assert_payload_time_array(scanner_inputs, "leader_start_time_features", len(BAR_TIME_FEATURE_COLUMNS), "scanner_inputs.leader_start_time_features")
        _assert_payload_time_array(scanner_inputs, "leader_end_time_features", len(BAR_END_FEATURE_COLUMNS), "scanner_inputs.leader_end_time_features")
        _assert_payload_time_array(scanner_inputs, "origin_time_features", len(BAR_TIME_FEATURE_COLUMNS), "scanner_inputs.origin_time_features")
        _assert_payload_time_array(scanner_inputs, "origin_start_time_features", len(BAR_TIME_FEATURE_COLUMNS), "scanner_inputs.origin_start_time_features")
        _assert_payload_time_array(scanner_inputs, "origin_end_time_features", len(BAR_END_FEATURE_COLUMNS), "scanner_inputs.origin_end_time_features")
        names = tuple(str(name) for name in scanner_inputs.get("time_feature_names", ()))
        if names and names != BAR_TIME_FEATURE_COLUMNS:
            raise RuntimeError("scanner_inputs.time_feature_names does not match the loader time-feature contract.")
        start_names = tuple(str(name) for name in scanner_inputs.get("start_time_feature_names", ()))
        if start_names and start_names != BAR_TIME_FEATURE_COLUMNS:
            raise RuntimeError("scanner_inputs.start_time_feature_names does not match the loader time-feature contract.")
        end_names = tuple(str(name) for name in scanner_inputs.get("end_time_feature_names", ()))
        if end_names and end_names != BAR_END_FEATURE_COLUMNS:
            raise RuntimeError("scanner_inputs.end_time_feature_names does not match the loader time-feature contract.")


def _payload_array_has_rows(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    return isinstance(value, np.ndarray) and value.size > 0


def _assert_payload_time_array(payload: Mapping[str, Any], key: str, expected_width: int, label: str) -> None:
    value = payload.get(key)
    if not isinstance(value, np.ndarray):
        raise RuntimeError(f"Missing required time feature array: {label}.")
    if value.ndim < 1 or int(value.shape[-1]) != int(expected_width):
        width = int(value.shape[-1]) if value.ndim else 0
        raise RuntimeError(f"{label} width is {width}, expected {int(expected_width)}.")


def _merge_external_context(batches: Sequence[DailyIndexTrainingBatch]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for batch in batches:
        for key, value in batch.external_context.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            else:
                merged[key] = value
    return merged


def _count_batch_samples_for_part_keys(batch: DailyIndexTrainingBatch, part_keys: set[str]) -> int:
    if batch.source_part_key.shape[0] == 0 or not part_keys:
        return 0
    return int(np.count_nonzero(np.isin(batch.source_part_key.astype(str, copy=False), list(part_keys))))


def _collect_ordered_futures(
    executor: ThreadPoolExecutor,
    fn: Any,
    items: Sequence[Any],
    *,
    stop_event: threading.Event | None = None,
) -> list[Any]:
    futures = [executor.submit(fn, item) for item in items]
    pending = set(futures)
    try:
        while pending:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
        return [future.result() for future in futures]
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in futures:
            future.cancel()
        raise


def _materialize_bounded(
    executor: ThreadPoolExecutor,
    materializer: DailyIndexBatchMaterializer,
    loaded: Sequence[LoadedDailyIndexPart],
    batches: Iterator[list[DailyIndexSampleRef]],
    *,
    preserve_order: bool = True,
    stop_event: threading.Event | None = None,
) -> Iterator[DailyIndexTrainingBatch]:
    max_pending = max(1, int(getattr(executor, "_max_workers", 1)))
    pending_all: set[Future[DailyIndexTrainingBatch]] = set()
    if preserve_order:
        pending_ordered: deque[Future[DailyIndexTrainingBatch]] = deque()

        def submit_until_full_ordered() -> None:
            while len(pending_ordered) < max_pending:
                if stop_event is not None and stop_event.is_set():
                    raise KeyboardInterrupt()
                try:
                    refs = next(batches)
                except StopIteration:
                    return
                future = executor.submit(materializer.materialize, loaded, refs)
                pending_ordered.append(future)
                pending_all.add(future)

        try:
            submit_until_full_ordered()
            while pending_ordered:
                if stop_event is not None and stop_event.is_set():
                    raise KeyboardInterrupt()
                future = pending_ordered[0]
                wait_start = time.perf_counter()
                while not future.done():
                    if stop_event is not None and stop_event.is_set():
                        raise KeyboardInterrupt()
                    wait({future}, timeout=0.2)
                pending_ordered.popleft()
                pending_all.discard(future)
                batch = future.result()
                batch.profile["materialize_wait_seconds"] = float(batch.profile.get("materialize_wait_seconds", 0.0)) + (time.perf_counter() - wait_start)
                yield batch
                submit_until_full_ordered()
        except BaseException:
            if stop_event is not None:
                stop_event.set()
            for future in pending_all:
                future.cancel()
            raise
        return

    pending: set[Future[DailyIndexTrainingBatch]] = set()

    def submit_until_full() -> None:
        while len(pending) < max_pending:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            try:
                refs = next(batches)
            except StopIteration:
                return
            future = executor.submit(materializer.materialize, loaded, refs)
            pending.add(future)
            pending_all.add(future)

    try:
        submit_until_full()
        while pending:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                pending_all.discard(future)
                wait_start = time.perf_counter()
                batch = future.result()
                batch.profile["materialize_wait_seconds"] = float(batch.profile.get("materialize_wait_seconds", 0.0)) + (time.perf_counter() - wait_start)
                yield batch
            submit_until_full()
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in pending_all:
            future.cancel()
        raise


def _materialize_chronological_bounded(
    executor: ThreadPoolExecutor,
    materializer: DailyIndexBatchMaterializer,
    loaded: Sequence[LoadedDailyIndexPart],
    batches: Iterator[list[DailyIndexSampleRef]],
    *,
    event_cache: "_RollingEventStreamCache",
    context_cache: "_RollingContextTensorCache | None" = None,
    preserve_order: bool = True,
    stop_event: threading.Event | None = None,
    day_profile: Mapping[str, float | int] | None = None,
    telemetry_callback: Callable[..., None] | None = None,
    startup_pending_chunks: int | None = None,
) -> Iterator[DailyIndexTrainingBatch]:
    max_pending = max(1, int(getattr(executor, "_max_workers", 1)))
    startup_pending = min(max_pending, max(1, int(startup_pending_chunks or 2)))
    pending_ordered: deque[Future[DailyIndexTrainingBatch]] = deque()
    pending_all: set[Future[DailyIndexTrainingBatch]] = set()
    yielded_chunks = 0

    def submit_until_full(target_pending: int) -> None:
        target = max(1, min(max_pending, int(target_pending)))
        while len(pending_ordered) < target:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            try:
                refs = next(batches)
            except StopIteration:
                return
            raw_stream, names, profile = event_cache.materialize(loaded, refs)
            context_override = context_cache.materialize(materializer, loaded, refs) if context_cache is not None else None
            merged_profile = {
                **dict(day_profile or {}),
                **profile,
                **(context_override.profile if context_override is not None else {}),
                "prefetch_materialize_max_pending_batches": int(max_pending),
                "prefetch_materialize_pending_batches": int(len(pending_ordered)),
                "prefetch_materialize_startup_pending_batches": int(startup_pending),
            }
            future = executor.submit(
                materializer.materialize,
                loaded,
                refs,
                raw_stream_override=raw_stream,
                raw_stream_feature_names_override=names,
                profile_override=merged_profile,
                context_override=context_override,
            )
            pending_ordered.append(future)
            pending_all.add(future)
            if telemetry_callback is not None:
                telemetry_callback(
                    loader_phase="materialize_pending",
                    prefetch_materialize_max_pending_batches=int(max_pending),
                    prefetch_materialize_pending_batches=int(len(pending_ordered)),
                )

    try:
        submit_until_full(startup_pending)
        while pending_ordered:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            if preserve_order:
                future = pending_ordered[0]
                wait_start = time.perf_counter()
                while not future.done():
                    if stop_event is not None and stop_event.is_set():
                        raise KeyboardInterrupt()
                    wait({future}, timeout=0.2)
                pending_ordered.popleft()
                pending_all.discard(future)
                batch = future.result()
                batch.profile["materialize_wait_seconds"] = float(batch.profile.get("materialize_wait_seconds", 0.0)) + (time.perf_counter() - wait_start)
                if telemetry_callback is not None:
                    telemetry_callback(
                        loader_phase="materialize_done",
                        prefetch_materialize_max_pending_batches=int(max_pending),
                        prefetch_materialize_pending_batches=int(len(pending_ordered)),
                    )
                yielded_chunks += 1
                yield batch
            else:
                done, _ = wait(set(pending_ordered), timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in list(done):
                    try:
                        pending_ordered.remove(future)
                    except ValueError:
                        pass
                    pending_all.discard(future)
                    if telemetry_callback is not None:
                        telemetry_callback(
                            loader_phase="materialize_done",
                            prefetch_materialize_max_pending_batches=int(max_pending),
                            prefetch_materialize_pending_batches=int(len(pending_ordered)),
                        )
                    yielded_chunks += 1
                    yield future.result()
            submit_until_full(max_pending if yielded_chunks >= startup_pending else startup_pending)
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in pending_all:
            future.cancel()
        raise


class _RollingContextTensorCache:
    def __init__(self, *, config: DailyIndexLoaderConfig) -> None:
        self.config = config
        self.capacity = max(1, int(config.ticker_cache_capacity))
        self._state_by_key: dict[tuple[str, str], _RollingContextState] = {}
        self._global_state_by_key: dict[str, _RollingContextState] = {}
        self._source_timestamp_cache: dict[tuple[int, str], np.ndarray] = {}
        self._signature_cache: dict[tuple[Any, ...], Any] = {}
        self._evictions = 0
        self._last_evicted_key = ""
        self._last_window_refs = 0
        self._last_window_tickers = 0
        self._last_context_seconds = 0.0
        self._last_context_bytes = 0
        self._last_warm_tickers = 0

    def clear(self) -> None:
        self._state_by_key.clear()
        self._global_state_by_key.clear()
        self._source_timestamp_cache.clear()
        self._signature_cache.clear()
        self._last_evicted_key = ""
        self._last_window_refs = 0
        self._last_window_tickers = 0
        self._last_context_seconds = 0.0
        self._last_context_bytes = 0

    def telemetry_snapshot(self) -> dict[str, int | float]:
        ticker_state_count = int(len({ticker for ticker, _group in self._state_by_key}))
        context_state_count = int(len(self._state_by_key))
        global_state_count = int(len(self._global_state_by_key))
        estimated_bytes = int(sum(int(state.estimated_bytes) for state in self._state_by_key.values()))
        estimated_bytes += int(sum(int(state.estimated_bytes) for state in self._global_state_by_key.values()))
        return {
            "context_cache_ticker_states": int(ticker_state_count),
            "context_cache_modality_states": int(context_state_count),
            "context_cache_global_states": int(global_state_count),
            "context_cache_capacity": int(self.capacity),
            "context_cache_evictions": int(self._evictions),
            "context_cache_estimated_bytes": int(estimated_bytes),
            "context_cache_last_context_bytes": int(self._last_context_bytes),
            "context_cache_last_window_refs": int(self._last_window_refs),
            "context_cache_last_window_tickers": int(self._last_window_tickers),
            "context_cache_last_warm_tickers": int(self._last_warm_tickers),
            "context_cache_last_seconds": float(self._last_context_seconds),
        }

    def warm(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> dict[str, float | int]:
        start = time.perf_counter()
        first_ref_by_ticker: dict[str, DailyIndexSampleRef] = {}
        for ref in refs:
            ticker = str(parts[int(ref.part_index)].plan.ticker)
            if ticker not in first_ref_by_ticker:
                first_ref_by_ticker[ticker] = ref
        self._last_warm_tickers = int(len(first_ref_by_ticker))
        protected = set(first_ref_by_ticker)
        for ticker, ref in first_ref_by_ticker.items():
            part = parts[int(ref.part_index)]
            origin_ts = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[int(ref.origin_row)])
            source_date = str(part.plan.source_date)
            for group in self._ticker_context_groups():
                self._touch_ticker_state(
                    ticker=ticker,
                    group=group,
                    source_date=source_date,
                    timestamp_us=origin_ts,
                    estimated_bytes=0,
                )
        evicted = self._trim_capacity(protected_tickers=protected)
        return {
            "context_cache_warm_seconds": time.perf_counter() - start,
            "context_cache_warm_tickers": int(len(first_ref_by_ticker)),
            "context_cache_warm_evictions": int(evicted),
        }

    def materialize(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> _RollingContextBatch:
        start = time.perf_counter()
        self._signature_cache.clear()
        profile: dict[str, float | int] = {"rolling_context_rows": int(len(refs))}
        text_inputs: dict[str, dict[str, np.ndarray]] | None = None
        xbrl_inputs: dict[str, np.ndarray] | None = None
        corporate_inputs: dict[str, np.ndarray] | None = None
        bar_inputs: dict[str, dict[str, np.ndarray]] | None = None
        scanner_inputs: dict[str, np.ndarray] | None = None

        if TEXT_CONTEXT_GROUPS.intersection(set(self.config.data_groups)):
            section_start = time.perf_counter()
            text_inputs, text_profile = self._materialize_text_inputs_with_cache(materializer, parts, refs)
            profile.update({f"rolling_{key}": value for key, value in text_profile.items()})
            profile["rolling_text_seconds"] = time.perf_counter() - section_start
        if "xbrl" in self.config.data_groups:
            section_start = time.perf_counter()
            xbrl_inputs, xbrl_profile = self._materialize_xbrl_inputs_with_cache(materializer, parts, refs)
            profile.update({f"rolling_{key}": value for key, value in xbrl_profile.items()})
            profile["rolling_xbrl_seconds"] = time.perf_counter() - section_start
        if "corporate_actions" in self.config.data_groups:
            section_start = time.perf_counter()
            corporate_inputs, corporate_profile = self._materialize_corporate_action_inputs_with_cache(materializer, parts, refs)
            profile.update({f"rolling_{key}": value for key, value in corporate_profile.items()})
            profile["rolling_corporate_action_seconds"] = time.perf_counter() - section_start
        if BAR_CONTEXT_GROUPS.union(INTRADAY_BAR_CONTEXT_GROUPS).intersection(set(self.config.data_groups)):
            section_start = time.perf_counter()
            bar_inputs, bar_profile = self._materialize_bar_inputs_with_cache(materializer, parts, refs)
            profile.update({f"rolling_{key}": value for key, value in bar_profile.items()})
            profile["rolling_bar_seconds"] = time.perf_counter() - section_start
        if "scanner_context" in self.config.data_groups:
            section_start = time.perf_counter()
            scanner_inputs, scanner_profile = self._materialize_scanner_inputs_with_cache(materializer, parts, refs)
            profile.update({f"rolling_{key}": value for key, value in scanner_profile.items()})
            profile["rolling_scanner_seconds"] = time.perf_counter() - section_start

        profile["rolling_sparse_carryover_seconds"] = 0.0

        context_bytes = _nested_array_nbytes(text_inputs)
        context_bytes += _nested_array_nbytes(xbrl_inputs)
        context_bytes += _nested_array_nbytes(corporate_inputs)
        context_bytes += _nested_array_nbytes(bar_inputs)
        context_bytes += _nested_array_nbytes(scanner_inputs)
        self._commit_states(
            parts,
            refs,
            text_inputs=text_inputs,
            xbrl_inputs=xbrl_inputs,
            corporate_action_inputs=corporate_inputs,
            bar_inputs=bar_inputs,
            scanner_inputs=scanner_inputs,
            context_bytes=context_bytes,
        )
        elapsed = time.perf_counter() - start
        self._last_context_seconds = float(elapsed)
        self._last_context_bytes = int(context_bytes)
        profile.update(
            {
                "rolling_context_seconds": float(elapsed),
                "rolling_context_estimated_bytes": int(context_bytes),
                **self.telemetry_snapshot(),
            }
        )
        return _RollingContextBatch(
            text_inputs=text_inputs,
            xbrl_inputs=xbrl_inputs,
            corporate_action_inputs=corporate_inputs,
            bar_inputs=_strip_internal_cache_fields(bar_inputs) if bar_inputs is not None else None,
            scanner_inputs=_strip_internal_cache_fields(scanner_inputs) if scanner_inputs is not None else None,
            profile=profile,
        )

    def _materialize_text_inputs_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
        requested = [group for group in ("ticker_news_embeddings", "market_news_embeddings", "sec_filing_embeddings") if group in self.config.data_groups]
        if not requested:
            return {}, {}
        return self._materialize_sparse_with_cache(
            materializer,
            parts,
            refs,
            materialize_fn=materializer._materialize_text_inputs,
            requested_payloads=tuple(TEXT_INPUT_GROUP_TO_KEY[group] for group in requested),
            payload_to_group={
                "ticker_news": "ticker_news",
                "market_news": "market_news",
                "sec_filings": "sec_filings",
            },
            payload_to_source={
                "ticker_news": "ticker_news_embeddings",
                "market_news": "market_news_embeddings",
                "sec_filings": "sec_filing_embeddings",
            },
            global_payloads={"market_news"},
            refresh_fn=_refresh_text_payload_time_features,
            profile_prefix="text_cache",
        )

    def _materialize_xbrl_inputs_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "xbrl" not in self.config.data_groups:
            return {}, {}
        return self._materialize_single_sparse_with_cache(
            materializer,
            parts,
            refs,
            materialize_fn=materializer._materialize_xbrl_inputs,
            payload_group="xbrl",
            payload_source="xbrl",
            refresh_fn=_refresh_xbrl_payload_time_features,
            profile_prefix="xbrl_cache",
        )

    def _materialize_corporate_action_inputs_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "corporate_actions" not in self.config.data_groups:
            return {}, {}
        return self._materialize_single_sparse_with_cache(
            materializer,
            parts,
            refs,
            materialize_fn=materializer._materialize_corporate_action_inputs,
            payload_group="corporate_actions",
            payload_source="corporate_actions",
            refresh_fn=_refresh_corporate_action_payload_time_features,
            profile_prefix="corporate_action_cache",
        )

    def _materialize_bar_inputs_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, float | int]]:
        out: dict[str, dict[str, np.ndarray]] = {}
        profile: dict[str, float | int] = {}
        requested_daily = [BAR_INPUT_GROUP_TO_KEY[group] for group in ("daily_bars", "global_daily_bars") if group in self.config.data_groups]
        if requested_daily:
            daily_payloads, daily_profile = self._materialize_signature_payloads_with_cache(
                materializer,
                parts,
                refs,
                materialize_fn=materializer._materialize_bar_inputs,
                requested_payloads=tuple(requested_daily),
                payload_to_group={
                    "ticker_daily_bars": "ticker_daily_bars",
                    "global_daily_bars": "global_daily_bars",
                },
                global_payloads={"global_daily_bars"},
                signature_fn=lambda part, ref, payload_name: self._bar_payload_signature(materializer, part, ref, str(payload_name)),
                annotate_fn=lambda payloads, subset_parts, subset_refs: _annotate_bar_cache_payloads(materializer, payloads, subset_parts, subset_refs, self.config),
                refresh_fn=_refresh_bar_payload_time_features,
                profile_prefix="bar_cache",
            )
            out.update(daily_payloads)
            profile.update(daily_profile)
        if "intraday_bars" in self.config.data_groups:
            intraday_payloads, intraday_profile = self._materialize_signature_payloads_with_cache(
                materializer,
                parts,
                refs,
                materialize_fn=materializer._materialize_intraday_bar_inputs,
                requested_payloads=("ticker_intraday_bars",),
                payload_to_group={"ticker_intraday_bars": "ticker_intraday_bars"},
                global_payloads=set(),
                signature_fn=lambda part, ref, payload_name: self._bar_payload_signature(materializer, part, ref, str(payload_name)),
                annotate_fn=lambda payloads, subset_parts, subset_refs: _annotate_bar_cache_payloads(materializer, payloads, subset_parts, subset_refs, self.config),
                refresh_fn=_refresh_bar_payload_time_features,
                profile_prefix="intraday_bar_cache",
            )
            out.update(intraday_payloads)
            profile.update(intraday_profile)
        return out, profile

    def _materialize_scanner_inputs_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        if "scanner_context" not in self.config.data_groups:
            return {}, {}

        def wrapped_fn(
            subset_parts: Sequence[LoadedDailyIndexPart],
            subset_refs: Sequence[DailyIndexSampleRef],
        ) -> tuple[dict[str, Any], dict[str, float | int]]:
            payload, fn_profile = materializer._materialize_scanner_inputs(subset_parts, subset_refs)
            return {"scanner_context": payload}, fn_profile

        payloads, profile = self._materialize_signature_payloads_with_cache(
            materializer,
            parts,
            refs,
            materialize_fn=wrapped_fn,
            requested_payloads=("scanner_context",),
            payload_to_group={"scanner_context": "scanner_context"},
            global_payloads=set(),
            signature_fn=lambda part, ref, payload_name: self._scanner_payload_signature(materializer, part, ref),
            annotate_fn=lambda payloads, subset_parts, subset_refs: _annotate_scanner_cache_payload(materializer, payloads, subset_parts, subset_refs, self.config),
            refresh_fn=_refresh_scanner_payload_time_features,
            profile_prefix="scanner_cache",
        )
        return payloads.get("scanner_context", {}), profile

    def _materialize_single_sparse_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        materialize_fn: Callable[[Sequence[LoadedDailyIndexPart], Sequence[DailyIndexSampleRef]], tuple[dict[str, np.ndarray], dict[str, float | int]]],
        payload_group: str,
        payload_source: str,
        refresh_fn: Callable[[Mapping[str, Any], int, int], None],
        profile_prefix: str,
    ) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
        def wrapped_fn(
            subset_parts: Sequence[LoadedDailyIndexPart],
            subset_refs: Sequence[DailyIndexSampleRef],
        ) -> tuple[dict[str, Any], dict[str, float | int]]:
            payload, profile = materialize_fn(subset_parts, subset_refs)
            return {str(payload_group): payload}, profile

        payloads, profile = self._materialize_sparse_with_cache(
            materializer,
            parts,
            refs,
            materialize_fn=wrapped_fn,
            requested_payloads=(str(payload_group),),
            payload_to_group={str(payload_group): str(payload_group)},
            payload_to_source={str(payload_group): str(payload_source)},
            global_payloads=set(),
            refresh_fn=refresh_fn,
            profile_prefix=profile_prefix,
        )
        return payloads.get(str(payload_group), {}), profile

    def _materialize_signature_payloads_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        materialize_fn: Callable[[Sequence[LoadedDailyIndexPart], Sequence[DailyIndexSampleRef]], tuple[dict[str, Any], dict[str, float | int]]],
        requested_payloads: Sequence[str],
        payload_to_group: Mapping[str, str],
        global_payloads: set[str],
        signature_fn: Callable[[LoadedDailyIndexPart, DailyIndexSampleRef, str], Any],
        annotate_fn: Callable[[dict[str, Any], Sequence[LoadedDailyIndexPart], Sequence[DailyIndexSampleRef]], None],
        refresh_fn: Callable[[Mapping[str, Any], int, int], None],
        profile_prefix: str,
    ) -> tuple[dict[str, Any], dict[str, float | int]]:
        if not refs:
            payloads, fn_profile = materialize_fn(parts, refs)
            fn_profile[f"{profile_prefix}_hits"] = 0
            fn_profile[f"{profile_prefix}_misses"] = 0
            fn_profile[f"{profile_prefix}_stale"] = 0
            return payloads, fn_profile
        sample_count = int(len(refs))
        tickers, _ordinals, timestamps = _identity_arrays(parts, refs)
        output_by_payload: dict[str, Any] = {}
        missing_rows_by_payload: dict[str, list[int]] = {str(payload): [] for payload in requested_payloads}
        signature_by_payload_row: dict[tuple[str, int], Any] = {}
        hits = 0
        misses = 0
        stale = 0
        scan_start = time.perf_counter()
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            ticker = str(tickers[int(row)])
            origin_ts = int(timestamps[int(row)])
            for payload_name in requested_payloads:
                payload_name = str(payload_name)
                group = str(payload_to_group[payload_name])
                state_key = ("__global__", group) if payload_name in global_payloads else (ticker, group)
                is_global = payload_name in global_payloads
                signature = signature_fn(part, ref, payload_name)
                signature_by_payload_row[(payload_name, int(row))] = signature
                state = self._state_for_key(state_key, global_state=is_global)
                if state is not None and state.payload is not None and state.signature == signature and origin_ts >= int(state.timestamp_us):
                    if payload_name not in output_by_payload:
                        output_by_payload[payload_name] = _empty_batch_payload_like(state.payload, sample_count)
                    _restore_batch_row(output_by_payload[payload_name], state.payload, int(row), sample_count)
                    refresh_fn(output_by_payload[payload_name], int(row), origin_ts)
                    state.timestamp_us = origin_ts
                    state.source_date = str(part.plan.source_date)
                    hits += 1
                    continue
                if state is not None:
                    stale += 1
                missing_rows_by_payload[payload_name].append(int(row))
                misses += 1
        scan_seconds = time.perf_counter() - scan_start
        all_missing_rows = sorted({row for rows in missing_rows_by_payload.values() for row in rows})
        base_profile: dict[str, float | int] = {}
        materialized_by_payload: dict[str, Any] = {}
        if all_missing_rows:
            subset_refs = [refs[row] for row in all_missing_rows]
            miss_start = time.perf_counter()
            materialized_by_payload, base_profile = materialize_fn(parts, subset_refs)
            annotate_fn(materialized_by_payload, parts, subset_refs)
            base_profile[f"{profile_prefix}_miss_materialize_seconds"] = time.perf_counter() - miss_start
        else:
            base_profile[f"{profile_prefix}_miss_materialize_seconds"] = 0.0
        store_start = time.perf_counter()
        for payload_name in requested_payloads:
            payload_name = str(payload_name)
            if payload_name in materialized_by_payload:
                source_payload = materialized_by_payload[payload_name]
                if payload_name not in output_by_payload:
                    output_by_payload[payload_name] = _empty_batch_payload_from_batch(source_payload, sample_count, len(all_missing_rows))
                source_positions = {row: index for index, row in enumerate(all_missing_rows)}
                for target_row in missing_rows_by_payload[payload_name]:
                    source_row = source_positions[int(target_row)]
                    _copy_batch_row(output_by_payload[payload_name], source_payload, target_row=int(target_row), source_row=int(source_row), source_sample_count=len(all_missing_rows))
                    group = str(payload_to_group[payload_name])
                    ticker = str(tickers[int(target_row)])
                    state_key = ("__global__", group) if payload_name in global_payloads else (ticker, group)
                    is_global = payload_name in global_payloads
                    self._store_payload_state(
                        state_key,
                        group=group,
                        payload=_snapshot_batch_row(source_payload, int(source_row), len(all_missing_rows)),
                        global_state=is_global,
                        source_date="global" if is_global else str(parts[int(refs[int(target_row)].part_index)].plan.source_date),
                        timestamp_us=int(timestamps[int(target_row)]),
                        signature=signature_by_payload_row[(payload_name, int(target_row))],
                    )
            elif payload_name not in output_by_payload:
                output_by_payload[payload_name] = {}
        store_seconds = time.perf_counter() - store_start
        base_profile[f"{profile_prefix}_hits"] = int(hits)
        base_profile[f"{profile_prefix}_misses"] = int(misses)
        base_profile[f"{profile_prefix}_stale"] = int(stale)
        base_profile[f"{profile_prefix}_payloads"] = int(len(requested_payloads))
        base_profile[f"{profile_prefix}_scan_seconds"] = float(scan_seconds)
        base_profile[f"{profile_prefix}_store_seconds"] = float(store_seconds)
        base_profile[f"{profile_prefix}_hit_refresh_rows"] = int(hits)
        return output_by_payload, base_profile

    def _materialize_sparse_with_cache(
        self,
        materializer: DailyIndexBatchMaterializer,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        materialize_fn: Callable[[Sequence[LoadedDailyIndexPart], Sequence[DailyIndexSampleRef]], tuple[dict[str, Any], dict[str, float | int]]],
        requested_payloads: Sequence[str],
        payload_to_group: Mapping[str, str],
        payload_to_source: Mapping[str, str],
        global_payloads: set[str],
        refresh_fn: Callable[[Mapping[str, Any], int, int], None],
        profile_prefix: str,
    ) -> tuple[dict[str, Any], dict[str, float | int]]:
        if not refs:
            payloads, profile = materialize_fn(parts, refs)
            profile[f"{profile_prefix}_hits"] = 0
            profile[f"{profile_prefix}_misses"] = 0
            profile[f"{profile_prefix}_stale"] = 0
            return payloads, profile
        sample_count = int(len(refs))
        tickers, _ordinals, timestamps = _identity_arrays(parts, refs)
        output_by_payload: dict[str, Any] = {}
        missing_rows_by_payload: dict[str, list[int]] = {str(payload): [] for payload in requested_payloads}
        stale = 0
        hits = 0
        misses = 0
        scan_start = time.perf_counter()
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            ticker = str(tickers[int(row)])
            origin_ts = int(timestamps[int(row)])
            for payload_name in requested_payloads:
                payload_name = str(payload_name)
                group = str(payload_to_group[payload_name])
                source = str(payload_to_source[payload_name])
                state_key = ("__global__", group) if payload_name in global_payloads else (ticker, group)
                is_global = payload_name in global_payloads
                if self._cached_payload_is_fresh(state_key, part, source, origin_ts, global_state=is_global):
                    state = self._state_for_key(state_key, global_state=is_global)
                    if state is not None and state.payload is not None:
                        if payload_name not in output_by_payload:
                            output_by_payload[payload_name] = _empty_batch_payload_like(state.payload, sample_count)
                        _restore_batch_row(output_by_payload[payload_name], state.payload, int(row), sample_count)
                        refresh_fn(output_by_payload[payload_name], int(row), origin_ts)
                        hits += 1
                        continue
                if self._state_for_key(state_key, global_state=is_global) is not None:
                    stale += 1
                missing_rows_by_payload[payload_name].append(int(row))
                misses += 1
        scan_seconds = time.perf_counter() - scan_start
        all_missing_rows = sorted({row for rows in missing_rows_by_payload.values() for row in rows})
        base_profile: dict[str, float | int] = {}
        materialized_by_payload: dict[str, Any] = {}
        if all_missing_rows:
            subset_refs = [refs[row] for row in all_missing_rows]
            miss_start = time.perf_counter()
            materialized_by_payload, base_profile = materialize_fn(parts, subset_refs)
            base_profile[f"{profile_prefix}_miss_materialize_seconds"] = time.perf_counter() - miss_start
        else:
            base_profile[f"{profile_prefix}_miss_materialize_seconds"] = 0.0
        store_start = time.perf_counter()
        for payload_name in requested_payloads:
            payload_name = str(payload_name)
            if payload_name in materialized_by_payload:
                source_payload = materialized_by_payload[payload_name]
                if payload_name not in output_by_payload:
                    output_by_payload[payload_name] = _empty_batch_payload_from_batch(source_payload, sample_count, len(all_missing_rows))
                source_positions = {row: index for index, row in enumerate(all_missing_rows)}
                for target_row in missing_rows_by_payload[payload_name]:
                    source_row = source_positions[int(target_row)]
                    _copy_batch_row(output_by_payload[payload_name], source_payload, target_row=int(target_row), source_row=int(source_row), source_sample_count=len(all_missing_rows))
                    group = str(payload_to_group[payload_name])
                    ticker = str(tickers[int(target_row)])
                    state_key = ("__global__", group) if payload_name in global_payloads else (ticker, group)
                    is_global = payload_name in global_payloads
                    self._store_payload_state(
                        state_key,
                        group=group,
                        payload=_snapshot_batch_row(source_payload, int(source_row), len(all_missing_rows)),
                        global_state=is_global,
                        source_date="global" if is_global else str(parts[int(refs[int(target_row)].part_index)].plan.source_date),
                        timestamp_us=int(timestamps[int(target_row)]),
                    )
            elif payload_name not in output_by_payload:
                output_by_payload[payload_name] = {}
        store_seconds = time.perf_counter() - store_start
        base_profile[f"{profile_prefix}_hits"] = int(hits)
        base_profile[f"{profile_prefix}_misses"] = int(misses)
        base_profile[f"{profile_prefix}_stale"] = int(stale)
        base_profile[f"{profile_prefix}_payloads"] = int(len(requested_payloads))
        base_profile[f"{profile_prefix}_scan_seconds"] = float(scan_seconds)
        base_profile[f"{profile_prefix}_store_seconds"] = float(store_seconds)
        base_profile[f"{profile_prefix}_hit_refresh_rows"] = int(hits)
        return output_by_payload, base_profile

    def _cached_payload_is_fresh(
        self,
        state_key: tuple[str, str],
        part: LoadedDailyIndexPart,
        source_name: str,
        origin_timestamp_us: int,
        *,
        global_state: bool,
    ) -> bool:
        state = self._state_for_key(state_key, global_state=global_state)
        if state is None or state.payload is None:
            return False
        last_origin_ts = int(state.timestamp_us)
        origin_ts = int(origin_timestamp_us)
        if origin_ts < last_origin_ts:
            return False
        frame = part.context.get(str(source_name))
        if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
            return True
        timestamps = self._context_source_timestamps_us(frame, str(source_name))
        if timestamps.size <= 0:
            return True
        left = np.searchsorted(timestamps, last_origin_ts, side="right")
        right = np.searchsorted(timestamps, origin_ts, side="right")
        return int(right) <= int(left)

    def _state_for_key(self, state_key: tuple[str, str], *, global_state: bool) -> _RollingContextState | None:
        if global_state:
            return self._global_state_by_key.get(str(state_key[1]))
        return self._state_by_key.get((str(state_key[0]), str(state_key[1])))

    def _bar_payload_signature(
        self,
        materializer: DailyIndexBatchMaterializer,
        part: LoadedDailyIndexPart,
        ref: DailyIndexSampleRef,
        payload_name: str,
    ) -> Any:
        origin_row = int(ref.origin_row)
        origin_ts = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[origin_row])
        cache_key = (id(part), id(part.origins), str(payload_name), int(origin_row))
        cached = self._signature_cache.get(cache_key)
        if cached is not None:
            return cached
        if str(payload_name) == "ticker_intraday_bars":
            signature = _intraday_bar_cache_signature(part, origin_row, self.config)
        else:
            signature = _daily_bar_cache_signature(materializer, part, origin_ts, str(payload_name), self.config)
        self._signature_cache[cache_key] = signature
        return signature

    def _scanner_payload_signature(
        self,
        materializer: DailyIndexBatchMaterializer,
        part: LoadedDailyIndexPart,
        ref: DailyIndexSampleRef,
    ) -> Any:
        origin_row = int(ref.origin_row)
        cache_key = (id(part), id(part.origins), "scanner_context", int(origin_row))
        cached = self._signature_cache.get(cache_key)
        if cached is not None:
            return cached
        signature = _scanner_cache_signature(materializer, part, origin_row, self.config)
        self._signature_cache[cache_key] = signature
        return signature

    def _context_source_timestamps_us(self, frame: Any, source_name: str) -> np.ndarray:
        key = (id(frame), str(source_name))
        cached = self._source_timestamp_cache.get(key)
        if cached is not None:
            return cached
        timestamps = _context_source_timestamps_us(frame, str(source_name))
        self._source_timestamp_cache[key] = timestamps
        return timestamps

    def _commit_states(
        self,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        text_inputs: Mapping[str, Any] | None,
        xbrl_inputs: Mapping[str, Any] | None,
        corporate_action_inputs: Mapping[str, Any] | None,
        bar_inputs: Mapping[str, Any] | None,
        scanner_inputs: Mapping[str, Any] | None,
        context_bytes: int,
    ) -> None:
        if not refs:
            return
        tickers, _ordinals, timestamps = _identity_arrays(parts, refs)
        unique_tickers = tuple(dict.fromkeys(str(ticker) for ticker in tickers))
        self._last_window_refs = int(len(refs))
        self._last_window_tickers = int(len(unique_tickers))
        per_ticker_bytes = max(0, int(context_bytes) // max(1, int(len(unique_tickers))))
        last_source_by_ticker: dict[str, str] = {}
        last_timestamp_by_ticker: dict[str, int] = {}
        for ref in refs:
            part = parts[int(ref.part_index)]
            ticker = str(part.plan.ticker)
            origin_ts = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[int(ref.origin_row)])
            last_source_by_ticker[ticker] = str(part.plan.source_date)
            last_timestamp_by_ticker[ticker] = int(origin_ts)
        for ticker in unique_tickers:
            source_date = last_source_by_ticker.get(ticker, "")
            timestamp_us = int(last_timestamp_by_ticker.get(ticker, 0))
            for group in self._groups_present_for_ticker(
                text_inputs=text_inputs,
                xbrl_inputs=xbrl_inputs,
                corporate_action_inputs=corporate_action_inputs,
                bar_inputs=bar_inputs,
            ):
                self._touch_ticker_state(
                    ticker=ticker,
                    group=group,
                    source_date=source_date,
                    timestamp_us=timestamp_us,
                    estimated_bytes=per_ticker_bytes,
                )
        if text_inputs and "market_news" in text_inputs:
            self._touch_global_state("market_news", int(timestamps[-1]), int(_nested_array_nbytes(text_inputs.get("market_news"))))
        if bar_inputs and "global_daily_bars" in bar_inputs:
            self._touch_global_state("global_daily_bars", int(timestamps[-1]), int(_nested_array_nbytes(bar_inputs.get("global_daily_bars"))))
        if scanner_inputs:
            self._touch_global_state("scanner_context", int(timestamps[-1]), int(_nested_array_nbytes(scanner_inputs)))
        self._trim_capacity(protected_tickers=set(unique_tickers))

    def _apply_sparse_carryover(
        self,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
        *,
        text_inputs: dict[str, dict[str, np.ndarray]] | None,
        xbrl_inputs: dict[str, np.ndarray] | None,
        corporate_action_inputs: dict[str, np.ndarray] | None,
    ) -> dict[str, int]:
        if not refs:
            return {}
        carried = 0
        updated = 0
        tickers, _ordinals, timestamps = _identity_arrays(parts, refs)
        sample_count = int(len(refs))
        if text_inputs:
            for row in range(sample_count):
                ticker = str(tickers[row])
                origin_ts = int(timestamps[row])
                for key, group in (("ticker_news", "ticker_news"), ("sec_filings", "sec_filings")):
                    payload = text_inputs.get(key)
                    if not payload:
                        continue
                    state_key = (ticker, group)
                    mask = payload.get("chunk_mask")
                    available = bool(np.asarray(mask[row]).any()) if isinstance(mask, np.ndarray) and mask.shape[:1] else False
                    if available:
                        self._store_payload_state(state_key, group=group, payload=_snapshot_batch_row(payload, row, sample_count))
                        updated += 1
                    elif self._restore_payload_state(state_key, payload, row, sample_count):
                        _refresh_text_payload_time_features(payload, row, origin_ts)
                        carried += 1
                payload = text_inputs.get("market_news")
                if payload:
                    state_key = ("__global__", "market_news")
                    mask = payload.get("chunk_mask")
                    available = bool(np.asarray(mask[row]).any()) if isinstance(mask, np.ndarray) and mask.shape[:1] else False
                    if available:
                        self._store_payload_state(state_key, group="market_news", payload=_snapshot_batch_row(payload, row, sample_count), global_state=True)
                        updated += 1
                    elif self._restore_payload_state(state_key, payload, row, sample_count, global_state=True):
                        _refresh_text_payload_time_features(payload, row, origin_ts)
                        carried += 1
        if xbrl_inputs:
            mask = xbrl_inputs.get("mask")
            for row in range(sample_count):
                ticker = str(tickers[row])
                origin_ts = int(timestamps[row])
                state_key = (ticker, "xbrl")
                available = bool(np.asarray(mask[row]).any()) if isinstance(mask, np.ndarray) and mask.shape[:1] else False
                if available:
                    self._store_payload_state(state_key, group="xbrl", payload=_snapshot_batch_row(xbrl_inputs, row, sample_count))
                    updated += 1
                elif self._restore_payload_state(state_key, xbrl_inputs, row, sample_count):
                    _refresh_xbrl_payload_time_features(xbrl_inputs, row, origin_ts)
                    carried += 1
        if corporate_action_inputs:
            mask = corporate_action_inputs.get("mask")
            for row in range(sample_count):
                ticker = str(tickers[row])
                origin_ts = int(timestamps[row])
                state_key = (ticker, "corporate_actions")
                available = bool(np.asarray(mask[row]).any()) if isinstance(mask, np.ndarray) and mask.shape[:1] else False
                if available:
                    self._store_payload_state(state_key, group="corporate_actions", payload=_snapshot_batch_row(corporate_action_inputs, row, sample_count))
                    updated += 1
                elif self._restore_payload_state(state_key, corporate_action_inputs, row, sample_count):
                    _refresh_corporate_action_payload_time_features(corporate_action_inputs, row, origin_ts)
                    carried += 1
        return {
            "rolling_sparse_context_carried_rows": int(carried),
            "rolling_sparse_context_updated_rows": int(updated),
        }

    def _store_payload_state(
        self,
        state_key: tuple[str, str],
        *,
        group: str,
        payload: Any,
        global_state: bool = False,
        source_date: str = "",
        timestamp_us: int = 0,
        signature: Any | None = None,
    ) -> None:
        if global_state:
            state = self._global_state_by_key.get(str(state_key[1]))
            if state is None:
                self._global_state_by_key[str(state_key[1])] = _RollingContextState(
                    key=str(state_key[1]),
                    group=str(group),
                    source_date=str(source_date or "global"),
                    timestamp_us=int(timestamp_us),
                    estimated_bytes=int(_nested_array_nbytes(payload)),
                    payload=payload,
                    signature=signature,
                )
                return
            state.payload = payload
            state.source_date = str(source_date or state.source_date or "global")
            state.timestamp_us = int(timestamp_us)
            state.signature = signature
            state.estimated_bytes = max(int(state.estimated_bytes), int(_nested_array_nbytes(payload)))
            return
        state = self._state_by_key.get((str(state_key[0]), str(state_key[1])))
        if state is None:
            self._state_by_key[(str(state_key[0]), str(state_key[1]))] = _RollingContextState(
                key=f"{state_key[0]}:{state_key[1]}",
                group=str(group),
                source_date=str(source_date),
                timestamp_us=int(timestamp_us),
                estimated_bytes=int(_nested_array_nbytes(payload)),
                payload=payload,
                signature=signature,
            )
            return
        state.payload = payload
        state.source_date = str(source_date or state.source_date)
        state.timestamp_us = int(timestamp_us)
        state.signature = signature
        state.estimated_bytes = max(int(state.estimated_bytes), int(_nested_array_nbytes(payload)))

    def _restore_payload_state(
        self,
        state_key: tuple[str, str],
        payload: Any,
        row: int,
        sample_count: int,
        *,
        global_state: bool = False,
    ) -> bool:
        state = self._global_state_by_key.get(str(state_key[1])) if global_state else self._state_by_key.get((str(state_key[0]), str(state_key[1])))
        if state is None or state.payload is None:
            return False
        _restore_batch_row(payload, state.payload, int(row), int(sample_count))
        return True

    def _ticker_context_groups(self) -> tuple[str, ...]:
        groups: list[str] = []
        if "ticker_news_embeddings" in self.config.data_groups:
            groups.append("ticker_news")
        if "sec_filing_embeddings" in self.config.data_groups:
            groups.append("sec_filings")
        if "xbrl" in self.config.data_groups:
            groups.append("xbrl")
        if "corporate_actions" in self.config.data_groups:
            groups.append("corporate_actions")
        if "daily_bars" in self.config.data_groups:
            groups.append("ticker_daily_bars")
        if "intraday_bars" in self.config.data_groups:
            groups.append("ticker_intraday_bars")
        return tuple(groups)

    def _groups_present_for_ticker(
        self,
        *,
        text_inputs: Mapping[str, Any] | None,
        xbrl_inputs: Mapping[str, Any] | None,
        corporate_action_inputs: Mapping[str, Any] | None,
        bar_inputs: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        groups: list[str] = []
        if text_inputs:
            for key in ("ticker_news", "sec_filings"):
                if key in text_inputs:
                    groups.append(key)
        if xbrl_inputs:
            groups.append("xbrl")
        if corporate_action_inputs:
            groups.append("corporate_actions")
        if bar_inputs:
            for key in ("ticker_daily_bars", "ticker_intraday_bars"):
                if key in bar_inputs:
                    groups.append(key)
        return tuple(groups)

    def _touch_ticker_state(
        self,
        *,
        ticker: str,
        group: str,
        source_date: str,
        timestamp_us: int,
        estimated_bytes: int,
    ) -> None:
        key = (str(ticker), str(group))
        current = self._state_by_key.get(key)
        if current is None:
            self._state_by_key[key] = _RollingContextState(
                key=f"{ticker}:{group}",
                group=str(group),
                source_date=str(source_date),
                timestamp_us=int(timestamp_us),
                estimated_bytes=max(0, int(estimated_bytes)),
            )
            return
        current.source_date = str(source_date)
        current.timestamp_us = int(timestamp_us)
        current.estimated_bytes = max(int(current.estimated_bytes), max(0, int(estimated_bytes)))

    def _touch_global_state(self, group: str, timestamp_us: int, estimated_bytes: int) -> None:
        key = str(group)
        current = self._global_state_by_key.get(key)
        if current is None:
            self._global_state_by_key[key] = _RollingContextState(
                key=key,
                group=key,
                source_date="global",
                timestamp_us=int(timestamp_us),
                estimated_bytes=max(0, int(estimated_bytes)),
            )
            return
        current.timestamp_us = int(timestamp_us)
        current.estimated_bytes = max(int(current.estimated_bytes), max(0, int(estimated_bytes)))

    def _trim_capacity(self, *, protected_tickers: set[str]) -> int:
        resident_tickers = {ticker for ticker, _group in self._state_by_key}
        if len(resident_tickers) <= int(self.capacity):
            return 0
        evicted = 0
        while len({ticker for ticker, _group in self._state_by_key}) > int(self.capacity):
            candidates: dict[str, _RollingContextState] = {}
            for (ticker, _group), state in self._state_by_key.items():
                if ticker in protected_tickers:
                    continue
                existing = candidates.get(ticker)
                if existing is None or (str(state.source_date), int(state.timestamp_us), str(ticker)) < (
                    str(existing.source_date),
                    int(existing.timestamp_us),
                    str(ticker),
                ):
                    candidates[ticker] = state
            if not candidates:
                raise RuntimeError(
                    "Ticker context cache capacity exceeded and every resident ticker is protected: "
                    f"capacity={int(self.capacity):,} resident={len(resident_tickers):,} protected={len(protected_tickers):,}."
                )
            victim_ticker = min(
                candidates,
                key=lambda ticker: (str(candidates[ticker].source_date), int(candidates[ticker].timestamp_us), str(ticker)),
            )
            for key in [key for key in self._state_by_key if key[0] == victim_ticker]:
                self._state_by_key.pop(key, None)
            self._evictions += 1
            evicted += 1
            self._last_evicted_key = str(victim_ticker)
        return int(evicted)


@dataclass(slots=True)
class _RollingContextState:
    key: str
    group: str
    source_date: str
    timestamp_us: int
    estimated_bytes: int
    payload: Any | None = None
    signature: Any | None = None


class _RollingEventStreamCache:
    def __init__(self, *, config: DailyIndexLoaderConfig) -> None:
        self.config = config
        self.stream_length = max(1, int(config.event_stream_length))
        self.columns = tuple(str(column) for column in _event_columns_for_output(config))
        self.capacity = max(1, int(config.ticker_cache_capacity))
        self._state_by_ticker: dict[str, _RollingEventTickerState] = {}
        self._evictions = 0
        self._last_evicted_ticker = ""

    def clear(self) -> None:
        self._state_by_ticker.clear()
        self._last_evicted_ticker = ""

    def telemetry_snapshot(self) -> dict[str, int]:
        ticker_count = int(len(self._state_by_ticker))
        event_stream_bytes = int(self.stream_length * max(1, len(self.columns)) * np.dtype(np.float32).itemsize)
        ordinal_bytes = int(self.stream_length * np.dtype(np.int64).itemsize)
        return {
            "event_cache_ticker_states": ticker_count,
            "event_cache_capacity": int(self.capacity),
            "event_cache_evictions": int(self._evictions),
            "event_cache_stream_rows_per_ticker": int(self.stream_length),
            "event_cache_feature_count": int(len(self.columns)),
            "event_cache_estimated_bytes": int(ticker_count * (event_stream_bytes + ordinal_bytes)),
        }

    def warm(
        self,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> dict[str, float | int]:
        start = time.perf_counter()
        first_ref_by_ticker: dict[str, DailyIndexSampleRef] = {}
        for ref in refs:
            part = parts[int(ref.part_index)]
            ticker = str(part.plan.ticker)
            if ticker not in first_ref_by_ticker:
                first_ref_by_ticker[ticker] = ref
        rebuilt = 0
        appended = 0
        reused = 0
        protected_tickers = set(first_ref_by_ticker)
        for ticker, ref in first_ref_by_ticker.items():
            part = parts[int(ref.part_index)]
            origin_row = int(ref.origin_row)
            _, did_rebuild, appended_rows = self._ensure_state(part=part, origin_row=origin_row)
            if did_rebuild:
                rebuilt += 1
            elif appended_rows > 0:
                appended += 1
            else:
                reused += 1
            if ticker not in self._state_by_ticker:
                raise RuntimeError(f"Chronological event cache did not warm ticker {ticker}.")
        evicted = self._trim_capacity(protected_tickers=protected_tickers)
        return {
            "event_cache_warm_seconds": time.perf_counter() - start,
            "event_cache_warm_tickers": int(len(first_ref_by_ticker)),
            "event_cache_warm_rebuilds": int(rebuilt),
            "event_cache_warm_appends": int(appended),
            "event_cache_warm_reused": int(reused),
            "event_cache_warm_evictions": int(evicted),
            "event_cache_protected_tickers": int(len(protected_tickers)),
        }

    def materialize(
        self,
        parts: Sequence[LoadedDailyIndexPart],
        refs: Sequence[DailyIndexSampleRef],
    ) -> tuple[np.ndarray, tuple[str, ...], dict[str, float | int]]:
        start = time.perf_counter()
        out = np.empty((len(refs), self.stream_length, len(self.columns)), dtype=np.float32)
        rebuilt = 0
        appended = 0
        reused = 0
        copy_start = time.perf_counter()
        protected_tickers = {str(parts[int(ref.part_index)].plan.ticker) for ref in refs}
        grouped_rows = _rows_by_part(refs)
        for part_index, rows in grouped_rows.items():
            part = parts[int(part_index)]
            origin_rows = _origin_rows_for_refs(refs, rows)
            origin_ordinals = part.origin_array("origin_ordinal").astype(np.int64, copy=False)[origin_rows]
            origin_timestamps = part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[origin_rows]
            event_offsets = part.origin_array("event_row_offset").astype(np.int64, copy=False)[origin_rows]
            matrix = part.event_matrix(self.columns)
            ordinals = part.event_array("ordinal").astype(np.int64, copy=False)
            ticker = str(part.plan.ticker)
            for local_index, output_row in enumerate(rows):
                origin_ordinal = int(origin_ordinals[int(local_index)])
                origin_timestamp = int(origin_timestamps[int(local_index)])
                event_offset = int(event_offsets[int(local_index)])
                state = self._state_by_ticker.get(ticker)
                if state is None or not state.can_append(origin_ordinal):
                    state = _RollingEventTickerState.from_part(
                        ticker=ticker,
                        stream_length=int(self.stream_length),
                        matrix=matrix,
                        ordinals=ordinals,
                        event_offset=event_offset,
                        part_key=_part_key(part.plan),
                        source_date=str(part.plan.source_date),
                        timestamp_us=origin_timestamp,
                    )
                    self._state_by_ticker[ticker] = state
                    rebuilt += 1
                else:
                    appended_rows = state.append_until(matrix=matrix, ordinals=ordinals, event_offset=event_offset, part_key=_part_key(part.plan))
                    if appended_rows > 0:
                        appended += int(appended_rows)
                    else:
                        reused += 1
                    state.touch(source_date=str(part.plan.source_date), timestamp_us=origin_timestamp)
                if int(state.last_ordinal) != origin_ordinal:
                    raise RuntimeError(f"Chronological event cache state did not advance to origin {origin_ordinal:,} for {_part_key(part.plan)}.")
                out[int(output_row)] = state.snapshot()
        copy_seconds = time.perf_counter() - copy_start
        evicted = self._trim_capacity(protected_tickers=protected_tickers)
        return out, self.columns, {
            "raw_stream_rolling_seconds": time.perf_counter() - start,
            "raw_stream_rolling_rebuilds": int(rebuilt),
            "raw_stream_rolling_appends": int(appended),
            "raw_stream_rolling_reused": int(reused),
            "raw_stream_rolling_ticker_states": int(len(self._state_by_ticker)),
            "raw_stream_rolling_evictions": int(evicted),
            "event_cache_protected_tickers": int(len(protected_tickers)),
            "raw_stream_rolling_state_copy_seconds": float(copy_seconds),
            "raw_stream_rolling_stateful": int(1),
        }

    def _ensure_state(
        self,
        *,
        part: LoadedDailyIndexPart,
        origin_row: int,
    ) -> tuple["_RollingEventTickerState", bool, int]:
        ticker = str(part.plan.ticker)
        origin_ordinal = int(part.origin_array("origin_ordinal").astype(np.int64, copy=False)[int(origin_row)])
        origin_timestamp_us = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[int(origin_row)])
        event_offset = int(part.origin_array("event_row_offset").astype(np.int64, copy=False)[int(origin_row)])
        matrix = part.event_matrix(self.columns)
        ordinals = part.event_array("ordinal").astype(np.int64, copy=False)
        if event_offset < 0 or event_offset >= int(ordinals.shape[0]):
            raise RuntimeError(f"Chronological event cache offset is out of bounds for {_part_key(part.plan)}.")
        if int(ordinals[event_offset]) != origin_ordinal:
            raise RuntimeError(f"Chronological event cache origin offset is misaligned for {_part_key(part.plan)}.")
        state = self._state_by_ticker.get(ticker)
        if state is None or not state.can_append(origin_ordinal):
            state = _RollingEventTickerState.from_part(
                ticker=ticker,
                stream_length=self.stream_length,
                matrix=matrix,
                ordinals=ordinals,
                event_offset=event_offset,
                part_key=_part_key(part.plan),
                source_date=str(part.plan.source_date),
                timestamp_us=int(origin_timestamp_us),
            )
            self._state_by_ticker[ticker] = state
            return state, True, 0
        appended = state.append_until(matrix=matrix, ordinals=ordinals, event_offset=event_offset, part_key=_part_key(part.plan))
        state.touch(source_date=str(part.plan.source_date), timestamp_us=int(origin_timestamp_us))
        return state, False, int(appended)

    def _trim_capacity(self, *, protected_tickers: set[str]) -> int:
        if len(self._state_by_ticker) <= int(self.capacity):
            return 0
        evicted = 0
        while len(self._state_by_ticker) > int(self.capacity):
            candidates = [
                state
                for ticker, state in self._state_by_ticker.items()
                if ticker not in protected_tickers
            ]
            if not candidates:
                raise RuntimeError(
                    "Ticker event cache capacity exceeded and every resident ticker is protected: "
                    f"capacity={int(self.capacity):,} resident={len(self._state_by_ticker):,} protected={len(protected_tickers):,}."
                )
            victim = min(candidates, key=lambda state: (str(state.last_used_source_date), int(state.last_used_timestamp_us), str(state.ticker)))
            self._state_by_ticker.pop(str(victim.ticker), None)
            self._evictions += 1
            evicted += 1
            self._last_evicted_ticker = str(victim.ticker)
        return int(evicted)


@dataclass(slots=True)
class _RollingEventTickerState:
    ticker: str
    stream: np.ndarray
    ordinals: np.ndarray
    last_ordinal: int
    last_used_source_date: str
    last_used_timestamp_us: int

    @classmethod
    def from_part(
        cls,
        *,
        ticker: str,
        stream_length: int,
        matrix: np.ndarray,
        ordinals: np.ndarray,
        event_offset: int,
        part_key: str,
        source_date: str,
        timestamp_us: int,
    ) -> "_RollingEventTickerState":
        start = int(event_offset) - int(stream_length) + 1
        if start < 0:
            raise RuntimeError(f"Chronological event cache lacks lookback rows for {part_key}.")
        window_ordinals = ordinals[start : int(event_offset) + 1].astype(np.int64, copy=False)
        if int(window_ordinals.shape[0]) != int(stream_length):
            raise RuntimeError(f"Chronological event cache built a short event stream for {part_key}.")
        if int(stream_length) > 1 and not bool(np.all(np.diff(window_ordinals) == 1)):
            raise RuntimeError(f"Chronological event cache crosses an ordinal gap for {part_key}.")
        return cls(
            ticker=str(ticker),
            stream=np.asarray(matrix[start : int(event_offset) + 1], dtype=np.float32).copy(),
            ordinals=window_ordinals.copy(),
            last_ordinal=int(window_ordinals[-1]),
            last_used_source_date=str(source_date),
            last_used_timestamp_us=int(timestamp_us),
        )

    def can_append(self, origin_ordinal: int) -> bool:
        return int(origin_ordinal) >= int(self.last_ordinal)

    def append_until(self, *, matrix: np.ndarray, ordinals: np.ndarray, event_offset: int, part_key: str) -> int:
        target_ordinal = int(ordinals[int(event_offset)])
        if target_ordinal == int(self.last_ordinal):
            return 0
        if target_ordinal < int(self.last_ordinal):
            raise RuntimeError(f"Chronological event cache moved backward for {part_key}.")
        append_start = int(np.searchsorted(ordinals, int(self.last_ordinal) + 1, side="left"))
        append_end = int(event_offset) + 1
        if append_start >= append_end:
            raise RuntimeError(f"Chronological event cache could not locate append rows for {part_key}.")
        append_ordinals = ordinals[append_start:append_end].astype(np.int64, copy=False)
        expected = np.arange(int(self.last_ordinal) + 1, int(target_ordinal) + 1, dtype=np.int64)
        if not bool(np.array_equal(append_ordinals, expected)):
            raise RuntimeError(f"Chronological event cache append crosses an ordinal gap for {part_key}.")
        append_rows = np.asarray(matrix[append_start:append_end], dtype=np.float32)
        count = int(append_rows.shape[0])
        if count >= int(self.stream.shape[0]):
            self.stream[:] = append_rows[-int(self.stream.shape[0]) :]
            self.ordinals[:] = append_ordinals[-int(self.ordinals.shape[0]) :]
        else:
            self.stream[:-count] = self.stream[count:]
            self.stream[-count:] = append_rows
            self.ordinals[:-count] = self.ordinals[count:]
            self.ordinals[-count:] = append_ordinals
        self.last_ordinal = int(target_ordinal)
        return count

    def touch(self, *, source_date: str, timestamp_us: int) -> None:
        self.last_used_source_date = str(source_date)
        self.last_used_timestamp_us = int(timestamp_us)

    def snapshot(self) -> np.ndarray:
        return self.stream


def _identity_arrays(parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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


def _source_part_keys(parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> np.ndarray:
    keys = np.empty((len(refs),), dtype=object)
    part_keys = [_part_key(part.plan) for part in parts]
    for part_index, rows in _rows_by_part(refs).items():
        keys[rows] = part_keys[int(part_index)]
    return keys


def _origin_event_offsets(parts: Sequence[LoadedDailyIndexPart], refs: Sequence[DailyIndexSampleRef]) -> np.ndarray:
    offsets = np.empty((len(refs),), dtype=np.int64)
    for part_index, rows in _rows_by_part(refs).items():
        part = parts[int(part_index)]
        origin_rows = _origin_rows_for_refs(refs, rows)
        offsets[rows] = part.origin_array("event_row_offset").astype(np.int64, copy=False)[origin_rows]
    return offsets


def _rows_by_part(refs: Sequence[DailyIndexSampleRef]) -> dict[int, np.ndarray]:
    rows: dict[int, list[int]] = {}
    for row, ref in enumerate(refs):
        rows.setdefault(int(ref.part_index), []).append(int(row))
    return {part_index: np.asarray(part_rows, dtype=np.int64) for part_index, part_rows in rows.items()}


def _origin_rows_for_refs(refs: Sequence[DailyIndexSampleRef], rows: np.ndarray) -> np.ndarray:
    return np.fromiter((int(refs[int(row)].origin_row) for row in rows), dtype=np.int64, count=int(rows.shape[0]))


def _plan_timestamp_start_us(plan: DailyIndexPartPlan) -> int:
    return int(getattr(plan, "origin_timestamp_start_us", 0) or 0)


def _plan_timestamp_end_us(plan: DailyIndexPartPlan) -> int:
    return int(getattr(plan, "origin_timestamp_end_us", 0) or 0)


def _day_origin_timestamp_bounds(
    plans: Sequence[DailyIndexPartPlan],
    *,
    start_us: int | None,
    end_us: int | None,
) -> tuple[int, int] | None:
    starts = [int(_plan_timestamp_start_us(plan)) for plan in plans if int(plan.origin_count) > 0 and _plan_timestamp_start_us(plan) > 0]
    ends = [int(_plan_timestamp_end_us(plan)) for plan in plans if int(plan.origin_count) > 0 and _plan_timestamp_end_us(plan) > 0]
    if not starts or not ends:
        return None
    day_start = min(starts)
    day_end = max(ends) + 1
    if start_us is not None:
        day_start = max(day_start, int(start_us))
    if end_us is not None:
        day_end = min(day_end, int(end_us))
    if day_start >= day_end:
        return None
    return int(day_start), int(day_end)


def _plan_intersects_window(plan: DailyIndexPartPlan, *, start_us: int, end_us: int) -> bool:
    if int(plan.origin_count) <= 0:
        return False
    plan_start = _plan_timestamp_start_us(plan)
    plan_end = _plan_timestamp_end_us(plan)
    if plan_start <= 0 or plan_end <= 0:
        return True
    return int(plan_start) < int(end_us) and int(plan_end) >= int(start_us)


@dataclass(slots=True)
class _OriginTickerCursor:
    plan: DailyIndexPartPlan
    chunk_rows: int
    next_file_row: int = 0
    chunk: Any | None = None
    row_pos: int = 0
    exhausted: bool = False
    chunks_loaded: int = 0
    rows_loaded: int = 0
    _timestamp_values: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=np.int64), repr=False)
    _ordinal_values: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=np.int64), repr=False)
    _event_offset_values: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=np.int64), repr=False)

    def load_next_chunk(self) -> int:
        if self.exhausted:
            return 0
        pl = _polars()
        origin_path = _plan_file_path(self.plan, self.plan.files["origins"])
        frame = (
            pl.scan_parquet(str(origin_path))
            .slice(int(self.next_file_row), max(1, int(self.chunk_rows)))
            .collect()
        )
        rows = int(frame.height)
        self.chunk = frame
        if rows > 0:
            self._timestamp_values = frame.get_column("origin_timestamp_us").to_numpy().astype(np.int64, copy=False)
            self._ordinal_values = frame.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
            self._event_offset_values = frame.get_column("event_row_offset").to_numpy().astype(np.int64, copy=False)
        else:
            self._timestamp_values = np.asarray([], dtype=np.int64)
            self._ordinal_values = np.asarray([], dtype=np.int64)
            self._event_offset_values = np.asarray([], dtype=np.int64)
        self.row_pos = 0
        self.next_file_row += rows
        self.chunks_loaded += int(rows > 0)
        self.rows_loaded += rows
        if rows <= 0 or rows < int(self.chunk_rows):
            self.exhausted = True
        return rows

    def _ensure_chunk(self) -> bool:
        while self.chunk is None or int(self.row_pos) >= int(self.chunk.height):
            if self.exhausted:
                return False
            self.load_next_chunk()
            if self.chunk is not None and int(self.chunk.height) > 0:
                return True
        return True

    def _timestamp_array(self) -> np.ndarray:
        if self.chunk is None:
            return np.asarray([], dtype=np.int64)
        return self._timestamp_values

    def current_sort_key(self) -> tuple[int, str, int] | None:
        if not self._ensure_chunk() or self.chunk is None:
            return None
        if int(self.row_pos) >= int(self.chunk.height):
            return None
        timestamp_us = int(self._timestamp_values[int(self.row_pos)])
        ordinal = int(self._ordinal_values[int(self.row_pos)])
        return timestamp_us, str(self.plan.ticker), ordinal

    def current_event_offset(self) -> int:
        if self.chunk is None or int(self.row_pos) >= int(self.chunk.height):
            return -1
        return int(self._event_offset_values[int(self.row_pos)])

    def current_origin_frame(self) -> Any | None:
        if not self._ensure_chunk() or self.chunk is None:
            return None
        if int(self.row_pos) >= int(self.chunk.height):
            return None
        return self.chunk.slice(int(self.row_pos), 1)

    def advance_one(self) -> bool:
        if not self._ensure_chunk():
            return False
        self.row_pos += 1
        return self._ensure_chunk()

    def advance_to(self, timestamp_us: int) -> bool:
        target = int(timestamp_us)
        while self._ensure_chunk():
            ts = self._timestamp_array()
            if int(self.row_pos) >= int(ts.shape[0]):
                continue
            local = int(np.searchsorted(ts[int(self.row_pos) :], target, side="left"))
            self.row_pos += local
            if int(self.row_pos) < int(ts.shape[0]):
                return True
        return False

    def seed_origin(self, start_us: int, *, min_event_offset: int = 0) -> Any | None:
        if not self.advance_to(int(start_us)):
            return None
        if self.chunk is None:
            return None
        min_offset = max(0, int(min_event_offset))
        while self._ensure_chunk():
            if self.chunk is None:
                return None
            offsets = self.chunk.get_column("event_row_offset").to_numpy().astype(np.int64, copy=False)
            while int(self.row_pos) < int(offsets.shape[0]) and int(offsets[int(self.row_pos)]) < min_offset:
                self.row_pos += 1
            if int(self.row_pos) < int(offsets.shape[0]):
                return self.chunk.slice(int(self.row_pos), 1)
        return None

    def telemetry(self) -> dict[str, int]:
        current_rows = int(self.chunk.height) if self.chunk is not None else 0
        return {
            "origin_cursor_chunks_loaded": int(self.chunks_loaded),
            "origin_cursor_rows_loaded": int(self.rows_loaded),
            "origin_cursor_current_rows": int(current_rows),
            "origin_cursor_next_file_row": int(self.next_file_row),
        }


def _build_day_origin_cursors(
    plans: Sequence[DailyIndexPartPlan],
    *,
    chunk_rows: int,
) -> list[_OriginTickerCursor]:
    return [_OriginTickerCursor(plan=plan, chunk_rows=max(1, int(chunk_rows))) for plan in plans]


def _load_origin_cursor_initial_chunks(
    *,
    read_pool: ThreadPoolExecutor,
    cursors: Sequence[_OriginTickerCursor],
    stop_event: threading.Event | None,
) -> dict[str, float | int]:
    start = time.perf_counter()
    rss_before = _process_rss_mib()
    futures = [read_pool.submit(cursor.load_next_chunk) for cursor in cursors]
    rows = 0
    chunks = 0
    try:
        for future in futures:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            loaded_rows = int(future.result())
            rows += loaded_rows
            chunks += int(loaded_rows > 0)
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in futures:
            future.cancel()
        raise
    rss_after = _process_rss_mib()
    return {
        "origin_cursor_initial_seconds": time.perf_counter() - start,
        "origin_cursor_count": int(len(cursors)),
        "origin_cursor_initial_chunks": int(chunks),
        "origin_cursor_rows_loaded": int(rows),
        "origin_cursor_chunk_rows": int(cursors[0].chunk_rows) if cursors else 0,
        "origin_cursor_rss_before_mib": float(rss_before),
        "origin_cursor_rss_after_mib": float(rss_after),
        "origin_cursor_rss_delta_mib": float(rss_after - rss_before),
    }


def _chronological_frontier_ref_cap(config: DailyIndexLoaderConfig, *, materialize_size: int) -> int:
    explicit = int(config.frontier_max_origins_per_window)
    if explicit > 0:
        return max(1, explicit)
    batch_size = max(1, int(config.batch_size))
    chunk_size = max(1, int(materialize_size))
    workers = max(1, int(config.materialize_workers))
    payload_scale = max(1, int(config.loaded_parts_per_group)) * 64
    auto = max(batch_size * 16, chunk_size * workers * 4, payload_scale, 4_096)
    return int(min(max(auto, batch_size), 65_536))


def _frontier_key_after(candidate: tuple[int, str, int], after_key: tuple[int, str, int] | None) -> bool:
    if after_key is None:
        return True
    return (int(candidate[0]), str(candidate[1]), int(candidate[2])) > (int(after_key[0]), str(after_key[1]), int(after_key[2]))


def _cursor_origin_is_candidate(
    cursor: _OriginTickerCursor,
    *,
    config: DailyIndexLoaderConfig,
    seed: int,
    dataset_plan_id: str,
) -> bool:
    key = cursor.current_sort_key()
    if key is None:
        return False
    timestamp_us, _ticker, ordinal = key
    if config.event_output_mode == "raw_stream" and int(cursor.current_event_offset()) < (int(config.event_stream_length) - 1):
        return False
    if _uses_dataset_hash_filter(config):
        return _sample_selected(
            cursor.plan,
            int(ordinal),
            int(timestamp_us),
            config=config,
            seed=int(seed),
            dataset_plan_id=str(dataset_plan_id),
        )
    return True


def _advance_cursor_to_frontier_candidate(
    cursor: _OriginTickerCursor,
    *,
    start_us: int,
    end_us: int,
    after_key: tuple[int, str, int] | None,
    config: DailyIndexLoaderConfig,
    seed: int,
    dataset_plan_id: str,
) -> tuple[int, str, int] | None:
    if not cursor.advance_to(int(start_us)):
        return None
    while True:
        key = cursor.current_sort_key()
        if key is None:
            return None
        if int(key[0]) >= int(end_us):
            return None
        if _frontier_key_after(key, after_key) and _cursor_origin_is_candidate(
            cursor,
            config=config,
            seed=seed,
            dataset_plan_id=dataset_plan_id,
        ):
            return key
        if not cursor.advance_one():
            return None


def _load_frontier_origins_from_cursors(
    *,
    cursors: Sequence[_OriginTickerCursor],
    start_us: int,
    end_us: int,
    after_key: tuple[int, str, int] | None,
    max_refs: int,
    config: DailyIndexLoaderConfig,
    seed: int,
    dataset_plan_id: str,
    stop_event: threading.Event | None,
) -> tuple[list[LoadedDailyIndexPart], list[DailyIndexSampleRef], dict[str, float | int]]:
    pl = _polars()
    start = time.perf_counter()
    rss_before = _process_rss_mib()
    cursor_rows_loaded_before = int(sum(cursor.rows_loaded for cursor in cursors))
    heap: list[tuple[int, str, int, int]] = []
    initialized = 0
    skipped_before_after_key = 0
    for index, cursor in enumerate(cursors):
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt()
        key = _advance_cursor_to_frontier_candidate(
            cursor,
            start_us=int(start_us),
            end_us=int(end_us),
            after_key=after_key,
            config=config,
            seed=seed,
            dataset_plan_id=dataset_plan_id,
        )
        if key is None:
            continue
        initialized += 1
        heapq.heappush(heap, (int(key[0]), str(key[1]), int(key[2]), int(index)))

    selected_frames: list[list[Any]] = []
    selected_counts: list[int] = []
    selected_cursor_to_part: dict[int, int] = {}
    refs: list[DailyIndexSampleRef] = []
    selected_last_key: tuple[int, str, int] | None = None
    cap = max(1, int(max_refs))
    stale_heap_entries = 0
    while heap and len(refs) < cap:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt()
        timestamp_us, ticker, ordinal, cursor_index = heapq.heappop(heap)
        cursor = cursors[int(cursor_index)]
        key = cursor.current_sort_key()
        if key is None:
            stale_heap_entries += 1
            continue
        if (int(key[0]), str(key[1]), int(key[2])) != (int(timestamp_us), str(ticker), int(ordinal)):
            stale_heap_entries += 1
            if int(key[0]) < int(end_us):
                heapq.heappush(heap, (int(key[0]), str(key[1]), int(key[2]), int(cursor_index)))
            continue
        if int(key[0]) >= int(end_us):
            continue
        if not _frontier_key_after(key, after_key):
            skipped_before_after_key += 1
            if cursor.advance_one():
                next_key = _advance_cursor_to_frontier_candidate(
                    cursor,
                    start_us=int(start_us),
                    end_us=int(end_us),
                    after_key=after_key,
                    config=config,
                    seed=seed,
                    dataset_plan_id=dataset_plan_id,
                )
                if next_key is not None:
                    heapq.heappush(heap, (int(next_key[0]), str(next_key[1]), int(next_key[2]), int(cursor_index)))
            continue
        frame = cursor.current_origin_frame()
        if frame is not None and int(getattr(frame, "height", 0) or 0) > 0:
            part_index = selected_cursor_to_part.get(int(cursor_index))
            if part_index is None:
                part_index = len(selected_frames)
                selected_cursor_to_part[int(cursor_index)] = int(part_index)
                selected_frames.append([])
                selected_counts.append(0)
            refs.append(DailyIndexSampleRef(part_index=int(part_index), origin_row=int(selected_counts[int(part_index)])))
            selected_frames[int(part_index)].append(frame)
            selected_counts[int(part_index)] += 1
            selected_last_key = key
        cursor.advance_one()
        next_key = _advance_cursor_to_frontier_candidate(
            cursor,
            start_us=int(start_us),
            end_us=int(end_us),
            after_key=after_key,
            config=config,
            seed=seed,
            dataset_plan_id=dataset_plan_id,
        )
        if next_key is not None:
            heapq.heappush(heap, (int(next_key[0]), str(next_key[1]), int(next_key[2]), int(cursor_index)))

    loaded_parts: list[LoadedDailyIndexPart] = []
    cursor_by_part = {part_index: cursor_index for cursor_index, part_index in selected_cursor_to_part.items()}
    for part_index, frames in enumerate(selected_frames):
        cursor_index = int(cursor_by_part[int(part_index)])
        loaded = LoadedDailyIndexPart(plan=cursors[cursor_index].plan)
        loaded.origins = frames[0] if len(frames) == 1 else pl.concat(frames, how="vertical")
        loaded_parts.append(loaded)

    rows = int(sum(int(part.origins.height) for part in loaded_parts if part.origins is not None))
    cursor_rows_loaded_after = int(sum(cursor.rows_loaded for cursor in cursors))
    rss_after = _process_rss_mib()
    cap_reached = int(len(refs) >= cap and bool(heap))
    first_key = _ref_sort_key(loaded_parts, refs[0]) if refs else None
    last_key = _ref_sort_key(loaded_parts, refs[-1]) if refs else selected_last_key
    profile: dict[str, float | int] = {
        "origin_window_cursor_seconds": time.perf_counter() - start,
        "origin_frontier_cursor_seconds": time.perf_counter() - start,
        "origin_frontier_mode": int(1),
        "origin_frontier_initialized_cursors": int(initialized),
        "origin_frontier_heap_remaining": int(len(heap)),
        "origin_frontier_cap_origins": int(cap),
        "origin_frontier_cap_reached": int(cap_reached),
        "origin_frontier_skipped_before_after_key": int(skipped_before_after_key),
        "origin_frontier_stale_heap_entries": int(stale_heap_entries),
        "origin_frontier_selected_refs": int(len(refs)),
        "origin_frontier_selected_parts": int(len(loaded_parts)),
        "origin_frontier_selected_tickers": int(len({part.plan.ticker for part in loaded_parts})),
        "origin_frontier_first_timestamp_us": int(first_key[0]) if first_key is not None else 0,
        "origin_frontier_last_timestamp_us": int(last_key[0]) if last_key is not None else 0,
        "origin_window_sort_seconds": 0.0,
        "origin_cache_parts": int(len(loaded_parts)),
        "origin_parts": int(len(loaded_parts)),
        "origin_rows": int(rows),
        "origin_cursor_count": int(len(cursors)),
        "origin_cursor_rows_loaded": int(cursor_rows_loaded_after),
        "origin_cursor_rows_loaded_for_window": int(cursor_rows_loaded_after - cursor_rows_loaded_before),
        "origin_window_rss_before_mib": float(rss_before),
        "origin_window_rss_after_mib": float(rss_after),
        "origin_window_rss_delta_mib": float(rss_after - rss_before),
    }
    return loaded_parts, refs, profile


def _load_event_seed_part(plan: DailyIndexPartPlan, origin_frame: Any, stream_length: int) -> LoadedDailyIndexPart:
    pl = _polars()
    loaded = LoadedDailyIndexPart(plan=plan)
    if origin_frame is None or int(getattr(origin_frame, "height", 0) or 0) <= 0:
        raise RuntimeError(f"Cannot warm event cache without an origin seed for {_part_key(plan)}.")
    event_offset = int(origin_frame.get_column("event_row_offset").to_numpy()[0])
    stream_length = max(1, int(stream_length))
    slice_start = max(0, int(event_offset) - int(stream_length) + 1)
    slice_len = int(event_offset) - int(slice_start) + 1
    loaded.events = (
        pl.scan_parquet(str(_plan_file_path(plan, plan.files["events"])))
        .slice(int(slice_start), int(slice_len))
        .collect()
    )
    adjusted = origin_frame.with_columns((pl.col("event_row_offset").cast(pl.Int64) - int(slice_start)).alias("event_row_offset"))
    loaded.origins = adjusted
    return loaded


def _load_context_seed_part(reader: DailyIndexPartReader, plan: DailyIndexPartPlan, origin_frame: Any) -> LoadedDailyIndexPart:
    loaded = LoadedDailyIndexPart(plan=plan)
    loaded.origins = origin_frame
    return reader.load_payload(
        loaded,
        load_events=False,
        load_labels=False,
        load_intraday_bars="intraday_bars" in reader.data_groups,
        load_corporate_labels=False,
    )


def _has_runtime_context_groups(config: DailyIndexLoaderConfig) -> bool:
    groups = set(config.data_groups)
    return bool(
        TEXT_CONTEXT_GROUPS.union(BAR_CONTEXT_GROUPS)
        .union(INTRADAY_BAR_CONTEXT_GROUPS)
        .union(XBRL_CONTEXT_GROUPS)
        .union(CORPORATE_ACTION_CONTEXT_GROUPS)
        .union(SCANNER_CONTEXT_GROUPS)
        .intersection(groups)
    )


def _warm_event_cache_for_day(
    *,
    read_pool: ThreadPoolExecutor,
    event_cache: "_RollingEventStreamCache",
    cursors: Sequence[_OriginTickerCursor],
    first_timestamp_us: int,
    stop_event: threading.Event | None,
    telemetry_callback: Callable[..., None] | None = None,
) -> dict[str, float | int]:
    start = time.perf_counter()
    rss_before = _process_rss_mib()
    seed_items: list[tuple[DailyIndexPartPlan, Any]] = []
    for cursor in cursors:
        seed = cursor.seed_origin(int(first_timestamp_us), min_event_offset=int(event_cache.stream_length) - 1)
        if seed is not None and int(getattr(seed, "height", 0) or 0) > 0:
            seed_items.append((cursor.plan, seed))
    total = int(len(seed_items))
    if total <= 0:
        return {
            "cache_first_day_warm_seconds": time.perf_counter() - start,
            "event_cache_warm_tickers": 0,
            "event_cache_warm_rows": 0,
            "event_cache_warm_evictions": 0,
        }
    max_pending = max(1, int(getattr(read_pool, "_max_workers", 1)) * 2)
    pending: set[Future[LoadedDailyIndexPart]] = set()
    next_index = 0
    warmed = 0
    event_rows = 0
    rebuilds = 0
    appends = 0
    reused = 0
    evictions = 0

    def submit_until_full() -> None:
        nonlocal next_index
        while next_index < total and len(pending) < max_pending:
            plan, origin_frame = seed_items[next_index]
            pending.add(read_pool.submit(_load_event_seed_part, plan, origin_frame, int(event_cache.stream_length)))
            next_index += 1

    try:
        submit_until_full()
        while pending:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if not done:
                if telemetry_callback is not None:
                    telemetry_callback(loader_phase="cache_warm_event", event_cache_warm_tickers=int(warmed), event_cache_warm_total_tickers=int(total))
                continue
            for future in done:
                loaded = future.result()
                profile = event_cache.warm([loaded], [DailyIndexSampleRef(part_index=0, origin_row=0)])
                warmed += 1
                event_rows += int(loaded.events.height) if loaded.events is not None else 0
                rebuilds += int(profile.get("event_cache_warm_rebuilds", 0) or 0)
                appends += int(profile.get("event_cache_warm_appends", 0) or 0)
                reused += int(profile.get("event_cache_warm_reused", 0) or 0)
                evictions += int(profile.get("event_cache_warm_evictions", 0) or 0)
                loaded.events = None
                loaded.origins = None
                loaded._event_arrays.clear()
                loaded._event_matrices.clear()
                loaded._origin_arrays.clear()
            if telemetry_callback is not None:
                telemetry_callback(loader_phase="cache_warm_event", event_cache_warm_tickers=int(warmed), event_cache_warm_total_tickers=int(total))
            submit_until_full()
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in pending:
            future.cancel()
        raise
    rss_after = _process_rss_mib()
    return {
        "cache_first_day_warm_seconds": time.perf_counter() - start,
        "event_cache_warm_tickers": int(warmed),
        "event_cache_warm_total_tickers": int(total),
        "event_cache_warm_rows": int(event_rows),
        "event_cache_warm_rebuilds": int(rebuilds),
        "event_cache_warm_appends": int(appends),
        "event_cache_warm_reused": int(reused),
        "event_cache_warm_evictions": int(evictions),
        "event_cache_warm_rss_before_mib": float(rss_before),
        "event_cache_warm_rss_after_mib": float(rss_after),
        "event_cache_warm_rss_delta_mib": float(rss_after - rss_before),
    }


def _warm_context_cache_for_day(
    *,
    read_pool: ThreadPoolExecutor,
    reader: DailyIndexPartReader,
    materializer: DailyIndexBatchMaterializer,
    context_cache: "_RollingContextTensorCache",
    cursors: Sequence[_OriginTickerCursor],
    first_timestamp_us: int,
    stop_event: threading.Event | None,
    telemetry_callback: Callable[..., None] | None = None,
) -> dict[str, float | int]:
    start = time.perf_counter()
    if not _has_runtime_context_groups(context_cache.config):
        return {
            "cache_first_day_context_warm_seconds": 0.0,
            "context_cache_warm_payload_tickers": 0,
            "context_cache_warm_payload_rows": 0,
        }
    rss_before = _process_rss_mib()
    seed_items: list[tuple[DailyIndexPartPlan, Any]] = []
    for cursor in cursors:
        seed = cursor.seed_origin(int(first_timestamp_us), min_event_offset=0)
        if seed is not None and int(getattr(seed, "height", 0) or 0) > 0:
            seed_items.append((cursor.plan, seed))
    total = int(len(seed_items))
    if total <= 0:
        return {
            "cache_first_day_context_warm_seconds": time.perf_counter() - start,
            "context_cache_warm_payload_tickers": 0,
            "context_cache_warm_payload_total_tickers": 0,
            "context_cache_warm_payload_rows": 0,
        }
    max_pending = max(1, int(getattr(read_pool, "_max_workers", 1)) * 2)
    materialize_chunk = max(1, min(128, int(getattr(read_pool, "_max_workers", 1)) * 4))
    pending: set[Future[LoadedDailyIndexPart]] = set()
    pending_all: set[Future[LoadedDailyIndexPart]] = set()
    loaded_chunk: list[LoadedDailyIndexPart] = []
    next_index = 0
    warmed = 0
    context_rows = 0
    context_bytes = 0
    materialize_seconds = 0.0

    def submit_until_full() -> None:
        nonlocal next_index
        while next_index < total and len(pending) < max_pending:
            plan, origin_frame = seed_items[next_index]
            future = read_pool.submit(_load_context_seed_part, reader, plan, origin_frame)
            pending.add(future)
            pending_all.add(future)
            next_index += 1

    def clear_loaded(loaded: LoadedDailyIndexPart) -> None:
        loaded.events = None
        loaded.origins = None
        loaded.windows = None
        loaded.labels = None
        loaded.context.clear()
        loaded.context_paths.clear()
        loaded._event_arrays.clear()
        loaded._event_matrices.clear()
        loaded._origin_arrays.clear()
        loaded._label_arrays.clear()
        loaded._context_arrays.clear()

    def flush_loaded_chunk() -> None:
        nonlocal warmed, context_rows, context_bytes, materialize_seconds
        if not loaded_chunk:
            return
        refs = [DailyIndexSampleRef(part_index=index, origin_row=0) for index in range(len(loaded_chunk))]
        chunk_start = time.perf_counter()
        context_batch = context_cache.materialize(materializer, loaded_chunk, refs)
        materialize_seconds += time.perf_counter() - chunk_start
        warmed += int(len(loaded_chunk))
        context_rows += int(context_batch.profile.get("rolling_context_rows", 0) or 0)
        context_bytes += int(context_batch.profile.get("rolling_context_estimated_bytes", 0) or 0)
        for loaded in loaded_chunk:
            clear_loaded(loaded)
        loaded_chunk.clear()

    try:
        submit_until_full()
        while pending:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt()
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if not done:
                if telemetry_callback is not None:
                    telemetry_callback(
                        loader_phase="cache_warm_context",
                        context_cache_warm_payload_tickers=int(warmed),
                        context_cache_warm_payload_total_tickers=int(total),
                    )
                continue
            for future in done:
                pending_all.discard(future)
                loaded_chunk.append(future.result())
                if len(loaded_chunk) >= materialize_chunk:
                    flush_loaded_chunk()
            if telemetry_callback is not None:
                telemetry_callback(
                    loader_phase="cache_warm_context",
                    context_cache_warm_payload_tickers=int(warmed),
                    context_cache_warm_payload_total_tickers=int(total),
                )
            submit_until_full()
        flush_loaded_chunk()
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in pending_all:
            future.cancel()
        for loaded in loaded_chunk:
            clear_loaded(loaded)
        raise
    rss_after = _process_rss_mib()
    return {
        "cache_first_day_context_warm_seconds": time.perf_counter() - start,
        "context_cache_warm_payload_tickers": int(warmed),
        "context_cache_warm_payload_total_tickers": int(total),
        "context_cache_warm_payload_rows": int(context_rows),
        "context_cache_warm_payload_bytes": int(context_bytes),
        "context_cache_warm_payload_materialize_seconds": float(materialize_seconds),
        "context_cache_warm_payload_rss_before_mib": float(rss_before),
        "context_cache_warm_payload_rss_after_mib": float(rss_after),
        "context_cache_warm_payload_rss_delta_mib": float(rss_after - rss_before),
    }


def _load_window_origins(
    *,
    read_pool: ThreadPoolExecutor,
    reader: DailyIndexPartReader,
    plans: Sequence[DailyIndexPartPlan],
    start_us: int,
    end_us: int,
    stop_event: threading.Event | None,
) -> list[LoadedDailyIndexPart]:
    futures = [
        read_pool.submit(_load_plan_window_origins, reader, plan, int(start_us), int(end_us))
        for plan in plans
    ]
    loaded_parts: list[LoadedDailyIndexPart] = []
    for future in futures:
        if stop_event is not None and stop_event.is_set():
            for pending in futures:
                pending.cancel()
            raise KeyboardInterrupt()
        loaded = future.result()
        if loaded is not None:
            loaded_parts.append(loaded)
    return loaded_parts


def _load_plan_window_origins(
    reader: DailyIndexPartReader,
    plan: DailyIndexPartPlan,
    start_us: int,
    end_us: int,
) -> LoadedDailyIndexPart | None:
    pl = _polars()
    origin_path = _plan_file_path(plan, plan.files["origins"])
    frame = (
        pl.scan_parquet(str(origin_path))
        .filter((pl.col("origin_timestamp_us") >= int(start_us)) & (pl.col("origin_timestamp_us") < int(end_us)))
        .collect()
    )
    if frame.height <= 0:
        return None
    loaded = LoadedDailyIndexPart(plan=plan)
    loaded.origins = frame
    return loaded


def _ref_origin_timestamp(parts: Sequence[LoadedDailyIndexPart], ref: DailyIndexSampleRef) -> int:
    part = parts[int(ref.part_index)]
    return int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[int(ref.origin_row)])


def _ref_sort_key(parts: Sequence[LoadedDailyIndexPart], ref: DailyIndexSampleRef) -> tuple[int, str, int]:
    part = parts[int(ref.part_index)]
    row = int(ref.origin_row)
    timestamp_us = int(part.origin_array("origin_timestamp_us").astype(np.int64, copy=False)[row])
    ordinal = int(part.origin_array("origin_ordinal").astype(np.int64, copy=False)[row])
    return timestamp_us, str(part.plan.ticker), ordinal


def _load_active_payloads(
    *,
    read_pool: ThreadPoolExecutor,
    reader: DailyIndexPartReader,
    loaded_origins: Sequence[LoadedDailyIndexPart],
    active_indices: Sequence[int],
    payload_cache: OrderedDict[str, LoadedDailyIndexPart],
    payload_cache_limit: int,
    stop_event: threading.Event | None,
) -> list[LoadedDailyIndexPart]:
    active_keys = tuple(_part_key(loaded_origins[int(index)].plan) for index in active_indices)
    protected_keys = set(active_keys)
    missing = [index for index, key in zip(active_indices, active_keys, strict=True) if key not in payload_cache]
    if missing:
        futures = [
            read_pool.submit(reader.load_payload, loaded_origins[int(index)])
            for index in missing
        ]
        for future in futures:
            if stop_event is not None and stop_event.is_set():
                for pending in futures:
                    pending.cancel()
                raise KeyboardInterrupt()
            loaded = future.result()
            key = _part_key(loaded.plan)
            payload_cache[key] = loaded
            payload_cache.move_to_end(key)
            _trim_payload_cache(payload_cache, limit=payload_cache_limit, protected_keys=protected_keys)
    out: list[LoadedDailyIndexPart] = []
    for index, key in zip(active_indices, active_keys, strict=True):
        loaded = payload_cache.get(key)
        if loaded is None:
            raise RuntimeError(f"Active payload was not loaded or was evicted unexpectedly: {key}")
        loaded.origins = loaded_origins[int(index)].origins
        loaded._origin_arrays.clear()
        payload_cache.move_to_end(key)
        out.append(loaded)
    _trim_payload_cache(payload_cache, limit=payload_cache_limit, protected_keys=protected_keys)
    return out


def _trim_payload_cache(payload_cache: OrderedDict[str, LoadedDailyIndexPart], *, limit: int, protected_keys: set[str]) -> None:
    target = max(1, int(limit))
    if len(payload_cache) <= target:
        return
    for key in list(payload_cache.keys()):
        if len(payload_cache) <= target:
            break
        if key in protected_keys:
            continue
        payload_cache.pop(key, None)


def _chronological_days_are_adjacent(previous: str, current: str, days: Sequence[tuple[str, Sequence[DailyIndexPartPlan]]]) -> bool:
    del days
    try:
        previous_day = dt.date.fromisoformat(str(previous)[:10])
        current_day = dt.date.fromisoformat(str(current)[:10])
    except ValueError:
        return False
    delta_days = (current_day - previous_day).days
    return 1 <= int(delta_days) <= 3


def _event_columns_for_output(config: DailyIndexLoaderConfig) -> tuple[str, ...]:
    if config.event_columns:
        return tuple(dict.fromkeys(str(column) for column in config.event_columns))
    suppressed = {str(column) for column in config.suppress_event_columns}
    return tuple(column for column in NUMERIC_EVENT_COLUMNS if column not in suppressed)


def _validated_event_columns_for_output(parts: Sequence[LoadedDailyIndexPart], config: DailyIndexLoaderConfig) -> tuple[str, ...]:
    columns = _event_columns_for_output(config)
    missing = [column for column in columns if not _all_parts_have_event_column(parts, column)]
    if missing and config.event_columns:
        raise RuntimeError(f"Requested event columns are missing from one or more loaded parts: {', '.join(missing)}")
    return tuple(column for column in columns if column not in missing)


def _all_parts_have_event_column(parts: Sequence[LoadedDailyIndexPart], column: str) -> bool:
    return all(part.events is not None and column in part.events.columns for part in parts)


def _event_column_dtype(parts: Sequence[LoadedDailyIndexPart], column: str) -> np.dtype:
    for part in parts:
        if part.events is not None and column in part.events.columns:
            return np.asarray(part.event_array(column)).dtype
    return np.float32


def _uses_dataset_hash_filter(config: DailyIndexLoaderConfig) -> bool:
    return (
        float(config.sample_fraction) < 1.0
        or int(config.sample_hash_modulus) > 0
        or bool(config.sample_hash_buckets)
    )


def _uses_fast_fraction_filter(config: DailyIndexLoaderConfig) -> bool:
    return (
        float(config.sample_fraction) < 1.0
        and int(config.sample_hash_modulus) <= 0
        and not bool(config.sample_hash_buckets)
    )


def _fast_fraction_candidate_rows(
    plan: DailyIndexPartPlan,
    candidate_rows: np.ndarray,
    *,
    config: DailyIndexLoaderConfig,
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
    plan: DailyIndexPartPlan,
    origin_ordinal: int,
    origin_timestamp_us: int,
    *,
    config: DailyIndexLoaderConfig,
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


def _sample_hash64(plan: DailyIndexPartPlan, origin_ordinal: int, origin_timestamp_us: int, *, seed: int, dataset_plan_id: str) -> int:
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


def _cache_plan_fingerprint(manifest: Mapping[str, Any], parts: Sequence[DailyIndexPartPlan]) -> str:
    payload = {
        "cache_format": str(manifest.get("cache_format", "")),
        "cache_version": int(manifest.get("cache_version") or 0),
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


def _dataset_plan_id(config: DailyIndexLoaderConfig, manifest_fingerprint: str) -> str:
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


def _part_key(plan: DailyIndexPartPlan) -> str:
    day = str(plan.source_date or "")
    return f"{plan.month}|{day}|{plan.ticker}|{int(plan.part_id)}"


def _cached_horizons(parts: Sequence[LoadedDailyIndexPart], *, config: DailyIndexLoaderConfig | None = None) -> tuple[str, ...]:
    for part in parts:
        horizons = part.plan.config.get("intraday_label_horizons") or ()
        if horizons:
            return tuple(str(item) for item in horizons)
    if config is not None:
        horizons = tuple(str(item) for item in getattr(config, "intraday_label_horizons", ()) if str(item).strip())
        if horizons:
            return horizons
    return ()


def _duration_us(name: str) -> int:
    text = str(name).strip().lower()
    if text in {"eod", "end_of_day", "end-of-day"}:
        return SESSION_LENGTH_US
    units = (
        ("ms", 1_000),
        ("us", 1),
        ("s", 1_000_000),
        ("m", 60_000_000),
        ("h", 3_600_000_000),
    )
    for suffix, scale in units:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    raise ValueError(f"Invalid intraday horizon {name!r}.")


def _intraday_label_resolution_us(horizon_name: str, horizon_us: int) -> int:
    if str(horizon_name).strip().lower() in {"eod", "end_of_day", "end-of-day"}:
        return 60_000_000
    if horizon_us <= 60_000_000:
        return 100_000
    if horizon_us <= 900_000_000:
        return 1_000_000
    if horizon_us <= 3_600_000_000:
        return 5_000_000
    if horizon_us <= 10_800_000_000:
        return 30_000_000
    return 60_000_000


def _intraday_horizon_specs(horizons: Sequence[str]) -> tuple[tuple[str, int, int, int, bool], ...]:
    specs: list[tuple[str, int, int, int, bool]] = []
    seen: set[str] = set()
    for raw in horizons:
        name = str(raw).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        is_eod = key in {"eod", "end_of_day", "end-of-day"}
        horizon_us = SESSION_LENGTH_US if is_eod else _duration_us(name)
        resolution_us = _intraday_label_resolution_us(name, horizon_us)
        if not is_eod and horizon_us % resolution_us:
            raise ValueError(f"Intraday horizon {name!r} is not aligned to {resolution_us:,}us grid.")
        specs.append((name, int(horizon_us), int(resolution_us), 0 if is_eod else int(horizon_us // resolution_us), bool(is_eod)))
    return tuple(sorted(specs, key=lambda item: (item[1], item[0])))


def _cached_intraday_context_horizons(parts: Sequence[LoadedDailyIndexPart], *, config: DailyIndexLoaderConfig | None = None) -> tuple[str, ...]:
    for part in parts:
        horizons = part.plan.config.get("intraday_context_horizons") or ()
        if horizons:
            return tuple(str(item) for item in horizons)
    for part in parts:
        frame = part.context.get("intraday_bars")
        if frame is None or int(getattr(frame, "height", 0) or 0) <= 0 or "horizon" not in getattr(frame, "columns", ()):
            continue
        row = frame.row(0, named=True)
        return tuple(str(item) for item in (row.get("horizon") or ()))
    if config is not None:
        horizons = tuple(str(item) for item in getattr(config, "intraday_label_horizons", ()) if str(item).strip())
        if horizons:
            return horizons
    return ()


def _intraday_bar_payload(
    values: np.ndarray,
    mask: np.ndarray,
    time_features: np.ndarray,
    end_time_features: np.ndarray,
    family_values: dict[str, np.ndarray],
    family_masks: dict[str, np.ndarray],
    family_time_features: dict[str, np.ndarray],
    family_end_time_features: dict[str, np.ndarray],
    horizons: tuple[str, ...],
) -> dict[str, np.ndarray]:
    payload: dict[str, np.ndarray] = {
        "values": values,
        "mask": mask,
        "time_features": time_features,
        "start_time_features": time_features,
        "end_time_features": end_time_features,
        "time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
        "start_time_feature_names": np.asarray(BAR_TIME_FEATURE_COLUMNS, dtype=object),
        "end_time_feature_names": np.asarray(BAR_END_FEATURE_COLUMNS, dtype=object),
        "horizons": np.asarray(horizons, dtype=object),
        "feature_names": np.asarray(BAR_FEATURE_KEYS, dtype=object),
    }
    for family in BAR_FAMILY_KEYS:
        payload[f"{family}_values"] = family_values[family]
        payload[f"{family}_mask"] = family_masks[family]
        payload[f"{family}_time_features"] = family_time_features[family]
        payload[f"{family}_start_time_features"] = family_time_features[family]
        payload[f"{family}_end_time_features"] = family_end_time_features[family]
        payload[f"{family}_feature_names"] = np.asarray(BAR_SOURCE_FEATURE_KEYS[family], dtype=object)
    return payload


def _cached_corporate_action_label_days(parts: Sequence[LoadedDailyIndexPart]) -> tuple[int, ...]:
    for part in parts:
        values = part.plan.config.get("corporate_action_label_days") or ()
        if values:
            return tuple(int(value) for value in values)
    for part in parts:
        labels = part.context.get("corporate_action_daily_labels")
        if labels is None or int(getattr(labels, "height", 0) or 0) <= 0 or "horizon_days" not in getattr(labels, "columns", ()):
            continue
        row = labels.row(0, named=True)
        raw = row.get("horizon_days") or ()
        return tuple(int(value) for value in raw)
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
    keys = tuple(LABEL_VALUE_DTYPES)
    if _labels_are_pivoted(labels):
        if right - left != 1:
            return None
        row = labels.row(left, named=True)
        values = {key: _cell_array(row.get(key)) if key in labels.columns else np.zeros((expected,), dtype=LABEL_VALUE_DTYPES.get(key, np.float32)) for key in keys}
    else:
        frame = labels.slice(left, right - left)
        if expected and frame.height != expected:
            return None
        values = {key: frame.get_column(key).to_numpy() if key in frame.columns else np.zeros((expected,), dtype=LABEL_VALUE_DTYPES.get(key, np.float32)) for key in keys}
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


def _prepare_intraday_compact_label_index(part: LoadedDailyIndexPart) -> IntradayCompactLabelIndex:
    frame = part.context.get("intraday_base_bars")
    bars: dict[tuple[str, int, str], IntradayBarFamilyIndex] = {}
    if frame is not None and int(getattr(frame, "height", 0) or 0) > 0:
        required = {"local_date", "label_resolution_us", "bucket_index", "bar_family", "open", "close", "high", "low", "event_count", "last_event_timestamp_us"}
        missing = sorted(required.difference(set(getattr(frame, "columns", ()))))
        if missing:
            raise RuntimeError(f"Compact intraday base bars are missing columns {missing} for {_part_key(part.plan)}.")
        for key, group in frame.sort(["local_date", "label_resolution_us", "bar_family", "bucket_index"]).partition_by(
            ["local_date", "label_resolution_us", "bar_family"],
            as_dict=True,
            maintain_order=True,
        ).items():
            local_date, resolution_us, family = key
            bucket = group.get_column("bucket_index").to_numpy().astype(np.int64, copy=False)
            event_count = group.get_column("event_count").to_numpy().astype(np.uint64, copy=False)
            size_sum = _optional_numeric_column(group, "size_sum", np.float32)
            bars[(str(local_date)[:10], int(resolution_us), str(family))] = IntradayBarFamilyIndex(
                buckets=bucket,
                open=group.get_column("open").to_numpy().astype(np.float32, copy=False),
                close=group.get_column("close").to_numpy().astype(np.float32, copy=False),
                high=group.get_column("high").to_numpy().astype(np.float32, copy=False),
                low=group.get_column("low").to_numpy().astype(np.float32, copy=False),
                size_sum=size_sum,
                size_open=_optional_numeric_column(group, "size_open", np.float32),
                size_close=_optional_numeric_column(group, "size_close", np.float32),
                size_high=_optional_numeric_column(group, "size_high", np.float32),
                size_low=_optional_numeric_column(group, "size_low", np.float32),
                event_count=event_count,
                last_event_timestamp_us=group.get_column("last_event_timestamp_us").to_numpy().astype(np.int64, copy=False),
                cum_size_sum=np.concatenate([np.zeros((1,), dtype=np.float64), np.cumsum(size_sum.astype(np.float64, copy=False))]),
                cum_event_count=np.concatenate([np.zeros((1,), dtype=np.uint64), np.cumsum(event_count.astype(np.uint64, copy=False))]),
            )
    condition_events: dict[tuple[str, str], tuple[np.ndarray, dict[str, np.ndarray]]] = {}
    condition_frame = part.context.get("intraday_condition_events")
    if condition_frame is not None and int(getattr(condition_frame, "height", 0) or 0) > 0 and "timestamp_us" in condition_frame.columns:
        for key, group in condition_frame.sort(["local_date", "timestamp_us"]).partition_by(["local_date"], as_dict=True, maintain_order=True).items():
            local_date = key[0] if isinstance(key, tuple) else key
            flags = {
                name: group.get_column(name).to_numpy().astype(np.bool_, copy=False)
                for name in ("condition_halt_pause_flag", "condition_resume_flag", "condition_news_risk_flag", "condition_luld_limit_state_flag")
                if name in group.columns
            }
            condition_events[(str(local_date)[:10], "flags")] = (
                group.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False),
                flags,
            )
    return IntradayCompactLabelIndex(
        bars=bars,
        condition_events=condition_events,
        ticker_news_by_date=_arrival_timestamps_by_local_date(part.context.get("ticker_news_embeddings")),
        sec_filing_by_date=_arrival_timestamps_by_local_date(part.context.get("sec_filing_embeddings")),
    )


def _optional_numeric_column(frame: Any, column: str, dtype: Any) -> np.ndarray:
    if column not in getattr(frame, "columns", ()):
        return np.zeros((int(getattr(frame, "height", 0) or 0),), dtype=dtype)
    return frame.get_column(column).to_numpy().astype(dtype, copy=False)


def _arrival_timestamps_by_local_date(frame: Any) -> dict[str, np.ndarray]:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0 or "timestamp_us" not in getattr(frame, "columns", ()):
        return {}
    pl = _polars()
    if "local_date" in frame.columns:
        source = frame.select(["local_date", "timestamp_us"]).unique().sort(["local_date", "timestamp_us"])
    else:
        source = (
            frame
            .select(
                [
                    pl.from_epoch(pl.col("timestamp_us"), time_unit="us").dt.convert_time_zone("America/New_York").dt.date().alias("local_date"),
                    "timestamp_us",
                ]
            )
            .unique()
            .sort(["local_date", "timestamp_us"])
        )
    out: dict[str, np.ndarray] = {}
    for key, group in source.partition_by(["local_date"], as_dict=True, maintain_order=True).items():
        local_date = key[0] if isinstance(key, tuple) else key
        out[str(local_date)[:10]] = group.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
    return out


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


def _bar_offsets_for_group(config: DailyIndexLoaderConfig, group: str) -> tuple[int, ...]:
    raw = config.global_daily_bar_offsets if str(group) == "global_daily_bars" else config.ticker_daily_bar_offsets
    offsets = tuple(sorted({max(1, int(value)) for value in raw}))
    return offsets or (1,)


def _prepare_daily_bar_context_index(frame: Any) -> DailyBarContextIndex:
    if frame is None or int(getattr(frame, "height", 0) or 0) <= 0:
        return DailyBarContextIndex(symbols=(), families=(), bar_start_ms_by_family_symbol={}, values_by_family_symbol={}, start_time_features_by_family_symbol={}, end_time_features_by_family_symbol={})
    missing = [column for column in ("sym", "bar_start_ms", *BAR_FEATURE_KEYS) if column not in getattr(frame, "columns", ())]
    if missing:
        raise RuntimeError(f"Daily bar context is missing required columns: {', '.join(missing)}")
    symbols_raw = frame.get_column("sym").to_numpy()
    symbols = np.asarray([str(symbol) for symbol in symbols_raw], dtype=object)
    if "bar_family" in getattr(frame, "columns", ()):
        families_raw = frame.get_column("bar_family").to_numpy()
        families = np.asarray([str(value) for value in families_raw], dtype=object)
    else:
        families = np.full((int(frame.height),), "trade", dtype=object)
    starts = frame.get_column("bar_start_ms").to_numpy().astype(np.int64, copy=False)
    values = frame.select(list(BAR_FEATURE_KEYS)).to_numpy().astype(np.float32, copy=False)
    order = np.lexsort((starts, symbols, families))
    families = families[order]
    symbols = symbols[order]
    starts = starts[order]
    values = values[order]
    start_time_features = _absolute_utc_time_feature_matrix(starts.astype(np.int64, copy=False) * 1000)
    end_time_features = _absolute_utc_time_feature_matrix((starts.astype(np.int64, copy=False) + 86_400_000) * 1000)
    unique_symbols = tuple(str(symbol) for symbol in np.unique(symbols))
    unique_families = tuple(str(family) for family in np.unique(families))
    starts_by_family_symbol: dict[str, dict[str, np.ndarray]] = {}
    values_by_family_symbol: dict[str, dict[str, np.ndarray]] = {}
    start_time_by_family_symbol: dict[str, dict[str, np.ndarray]] = {}
    end_time_by_family_symbol: dict[str, dict[str, np.ndarray]] = {}
    for family in unique_families:
        starts_by_family_symbol[family] = {}
        values_by_family_symbol[family] = {}
        start_time_by_family_symbol[family] = {}
        end_time_by_family_symbol[family] = {}
        family_rows = families == family
        family_symbols = symbols[family_rows]
        for symbol in tuple(str(value) for value in np.unique(family_symbols)):
            rows = family_rows & (symbols == symbol)
            starts_by_family_symbol[family][symbol] = starts[rows]
            values_by_family_symbol[family][symbol] = values[rows]
            start_time_by_family_symbol[family][symbol] = start_time_features[rows]
            end_time_by_family_symbol[family][symbol] = end_time_features[rows]
    return DailyBarContextIndex(
        symbols=unique_symbols,
        families=unique_families,
        bar_start_ms_by_family_symbol=starts_by_family_symbol,
        values_by_family_symbol=values_by_family_symbol,
        start_time_features_by_family_symbol=start_time_by_family_symbol,
        end_time_features_by_family_symbol=end_time_by_family_symbol,
    )


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


def _external_context_summary(parts: Sequence[LoadedDailyIndexPart]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for part in parts:
        for name, frame in part.context.items():
            summary.setdefault(name, 0)
            summary[name] += int(getattr(frame, "height", 0) or 0)
    return summary


def _empty_batch(mode: str) -> DailyIndexTrainingBatch:
    return DailyIndexTrainingBatch(
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

