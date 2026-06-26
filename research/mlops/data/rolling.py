from __future__ import annotations

import json
import datetime as dt
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string
from research.mlops.clickhouse_events import DEFAULT_CONTEXT_EVENTS, EVENT_ROW_DTYPE, PersistentClickHouseBytesClient, decode_price_array, encode_unified_event_window
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES, QUOTE_EVENT_TYPE, TRADE_EVENT_TYPE
from research.mlops.data.config import ExternalAsOfContextConfig, RollingMarketDataConfig
from research.mlops.data.contracts import (
    BAR_FEATURE_KEYS,
    ChunkWindowIndex,
    CompactEvent,
    FUTURE_BAR_FEATURE_KEYS,
    RollingProductionBatch,
    RollingSampleIndex,
    RollingTrainingBatch,
)
from research.mlops.data.market_events import events_to_rows
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler
from research.mlops.data.ticker_blocks import build_future_time_bar_labels


DAILY_MACRO_WINDOWS: tuple[tuple[str, int], ...] = (
    ("today_asof", 0),
    ("past_1d", 1),
    ("past_2d", 2),
    ("past_3d", 3),
    ("past_7d", 7),
    ("past_14d", 14),
    ("past_28d", 28),
    ("past_40d", 40),
    ("past_200d", 200),
)
DAILY_GLOBAL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("today_asof", 0),
    ("past_1d", 1),
    ("past_2d", 2),
    ("past_7d", 7),
)
DAILY_LABEL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("current_day_full", 0),
    ("plus_1d", 1),
    ("plus_2d", 2),
    ("plus_3d", 3),
    ("plus_7d", 7),
    ("plus_28d", 28),
)


@dataclass(frozen=True, slots=True)
class HistoricalDayFetchResult:
    rows_by_ticker: dict[str, np.ndarray]
    event_date: str
    rows_returned: int
    fetch_seconds: float


@dataclass(frozen=True, slots=True)
class RollingReadyIndexBlock:
    """Array-backed ready sample origins for one ticker.

    The heavy path is intentionally delayed. A large historical day can have
    millions of sample origins, and each origin has many context windows. The
    provider therefore stores only origin offsets here and creates
    `RollingSampleIndex` objects only for the current training/serving batch.
    """

    ticker: str
    rows: np.ndarray
    origin_offsets: np.ndarray

    @property
    def sample_count(self) -> int:
        return int(self.origin_offsets.shape[0])


@dataclass(frozen=True, slots=True)
class MacroBarFrame:
    """A small as-of lookup store for macro/global bar features.

    The frame is intentionally plain arrays instead of a model tensor. The
    temporal model can choose which fields to consume and normalize.
    """

    rows: list[dict[str, Any]]
    fetch_seconds: float = 0.0
    _index: dict[tuple[str, str], dict[str, np.ndarray]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in self.rows:
            key = (str(row.get("sym", "")).upper(), str(row.get("timeframe", "")))
            if key[0] and key[1]:
                grouped.setdefault(key, []).append(row)
        index: dict[tuple[str, str], dict[str, np.ndarray]] = {}
        for key, rows in grouped.items():
            rows.sort(key=lambda item: int(item.get("bar_start_ms", 0)))
            index[key] = {"bar_start_ms": np.asarray([int(row.get("bar_start_ms", 0)) for row in rows], dtype=np.int64)}
            for name in BAR_FEATURE_KEYS:
                index[key][name] = np.asarray([float(row.get(name, 0.0) or 0.0) for row in rows], dtype=np.float32)
        object.__setattr__(self, "_index", index)

    def asof(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> dict[str, float]:
        bars, _mask = self.asof_tensor(symbol=symbol, timestamp_us=timestamp_us, timeframes=tuple(timeframes))
        out: dict[str, float] = {}
        for row_index, timeframe in enumerate(tuple(str(value) for value in timeframes)):
            prefix = f"{timeframe}"
            for field_index, name in enumerate(BAR_FEATURE_KEYS):
                out[f"{prefix}_{name}"] = float(bars[row_index, field_index])
        return out

    def future(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> dict[str, float]:
        bars, _mask = self.future_tensor(symbol=symbol, timestamp_us=timestamp_us, timeframes=tuple(timeframes))
        out: dict[str, float] = {}
        for row_index, timeframe in enumerate(tuple(str(value) for value in timeframes)):
            prefix = f"{timeframe}"
            for field_index, name in enumerate(BAR_FEATURE_KEYS):
                out[f"{prefix}_{name}"] = float(bars[row_index, field_index])
        return out

    def asof_tensor(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
        return self._lookup_tensor(symbol=symbol, timestamp_us=timestamp_us, timeframes=tuple(timeframes), side="asof")

    def future_tensor(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
        return self._lookup_tensor(symbol=symbol, timestamp_us=timestamp_us, timeframes=tuple(timeframes), side="future")

    def daily_window_tensor(
        self,
        *,
        symbol: str,
        timestamp_us: int,
        windows: tuple[tuple[str, int], ...],
        today_asof: np.ndarray | None = None,
        today_asof_valid: bool = False,
    ) -> tuple[np.ndarray, np.ndarray]:
        symbol = symbol.upper()
        arrays = self._index.get((symbol, "1d"))
        bars = np.zeros((len(windows), len(BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((len(windows),), dtype=np.bool_)
        current_day_start_ms = _utc_day_start_ms(timestamp_us)
        completed_end = 0
        if arrays is not None and arrays["bar_start_ms"].size:
            completed_end = int(np.searchsorted(arrays["bar_start_ms"], current_day_start_ms, side="left"))
        for window_index, (name, count) in enumerate(windows):
            if name == "today_asof":
                if today_asof is not None and bool(today_asof_valid):
                    bars[window_index] = np.asarray(today_asof, dtype=np.float32)
                    mask[window_index] = True
                continue
            if arrays is None or int(count) <= 0:
                continue
            start = max(0, completed_end - int(count))
            end = completed_end
            if start >= end or end > arrays["bar_start_ms"].shape[0]:
                continue
            bars[window_index] = _aggregate_daily_bar_arrays(arrays, start=start, end=end)
            mask[window_index] = True
        return bars, mask

    def daily_future_tensor(
        self,
        *,
        symbol: str,
        timestamp_us: int,
        windows: tuple[tuple[str, int], ...],
    ) -> tuple[np.ndarray, np.ndarray]:
        symbol = symbol.upper()
        arrays = self._index.get((symbol, "1d"))
        bars = np.zeros((len(windows), len(BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((len(windows),), dtype=np.bool_)
        if arrays is None or arrays["bar_start_ms"].size == 0:
            return bars, mask
        current_day_start_ms = _utc_day_start_ms(timestamp_us)
        current_index = int(np.searchsorted(arrays["bar_start_ms"], current_day_start_ms, side="left"))
        if current_index >= arrays["bar_start_ms"].shape[0] or int(arrays["bar_start_ms"][current_index]) != current_day_start_ms:
            return bars, mask
        for window_index, (name, count) in enumerate(windows):
            if name == "current_day_full":
                bars[window_index] = _aggregate_daily_bar_arrays(arrays, start=current_index, end=current_index + 1)
                mask[window_index] = True
                continue
            if int(count) <= 0:
                continue
            start = current_index + 1
            end = start + int(count)
            if end > arrays["bar_start_ms"].shape[0]:
                continue
            bars[window_index] = _aggregate_daily_bar_arrays(arrays, start=start, end=end)
            mask[window_index] = True
        return bars, mask

    def _lookup_tensor(self, *, symbol: str, timestamp_us: int, timeframes: tuple[str, ...], side: str) -> tuple[np.ndarray, np.ndarray]:
        symbol = symbol.upper()
        timestamp_ms = int(timestamp_us) // 1000
        bars = np.zeros((len(timeframes), len(BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((len(timeframes),), dtype=np.bool_)
        for timeframe_index, timeframe_value in enumerate(timeframes):
            timeframe = str(timeframe_value)
            arrays = self._index.get((symbol, timeframe))
            if arrays is None or arrays["bar_start_ms"].size == 0:
                continue
            if side == "future":
                row_index = int(np.searchsorted(arrays["bar_start_ms"], timestamp_ms, side="right"))
                if row_index >= arrays["bar_start_ms"].shape[0]:
                    continue
            else:
                row_index = int(np.searchsorted(arrays["bar_start_ms"], timestamp_ms, side="right") - 1)
                if row_index < 0:
                    continue
            for field_index, name in enumerate(BAR_FEATURE_KEYS):
                bars[timeframe_index, field_index] = arrays[name][row_index]
            mask[timeframe_index] = True
        return bars, mask


@dataclass(slots=True)
class ExternalAsOfStore:
    """Generic as-of payload cache for future news/SEC/XBRL tables."""

    config: ExternalAsOfContextConfig
    rows_by_ticker: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            ticker = str(row.get(self.config.ticker_column, "")).upper()
            if not ticker:
                continue
            self.rows_by_ticker.setdefault(ticker, []).append(dict(row))
        for rows_for_ticker in self.rows_by_ticker.values():
            rows_for_ticker.sort(key=lambda item: _external_timestamp_us(item, self.config))

    def asof(self, *, ticker: str, timestamp_us: int) -> list[dict[str, Any]]:
        rows = self.rows_by_ticker.get(ticker.upper(), [])
        if not rows:
            return []
        lower_bound = None
        if int(self.config.max_age_microseconds) > 0:
            lower_bound = int(timestamp_us) - int(self.config.max_age_microseconds)
        selected: list[dict[str, Any]] = []
        for row in reversed(rows):
            row_ts = _external_timestamp_us(row, self.config)
            if row_ts > int(timestamp_us):
                continue
            if lower_bound is not None and row_ts < lower_bound:
                break
            selected.append(row)
            if len(selected) >= int(self.config.max_items):
                break
        selected.reverse()
        return selected


@dataclass(slots=True)
class RollingEmbeddingCache:
    """Production embedding lookup keyed by ticker and chunk-origin ordinal."""

    embedding_dim: int
    _values: dict[tuple[str, int], np.ndarray] = field(default_factory=dict)

    def add(self, *, ticker: str, origin_ordinal: int, embedding: np.ndarray) -> None:
        value = np.asarray(embedding, dtype=np.float32)
        if value.shape[-1] != int(self.embedding_dim):
            raise ValueError(f"Expected embedding_dim={self.embedding_dim}, got shape={value.shape}")
        self._values[(ticker.upper(), int(origin_ordinal))] = value

    def as_mapping(self) -> Mapping[tuple[str, int], np.ndarray]:
        return self._values


class _TextTokenizerAdapter:
    """Qwen tokenizer wrapper with a deterministic offline fallback.

    Training should use the configured Qwen tokenizer so text tensors are ready
    for the selected text encoder. The fallback keeps smoke tests and profiling
    runnable on machines where the model files are not cached yet; it is stable
    but is not a substitute for production tokenization.
    """

    def __init__(self, config: RollingMarketDataConfig) -> None:
        self.max_tokens = max(1, int(config.text_max_tokens))
        self.model_name = str(config.text_tokenizer_model)
        self.tokenizer: Any | None = None
        try:
            from transformers import AutoTokenizer  # type: ignore

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                local_files_only=bool(config.text_tokenizer_local_files_only),
            )
        except Exception as exc:
            if bool(config.strict_text_tokenizer):
                raise RuntimeError(f"Could not load tokenizer {self.model_name!r}") from exc
            self.tokenizer = None

    def encode(self, texts: list[str]) -> dict[str, np.ndarray]:
        if not texts:
            return {
                "input_ids": np.zeros((0, self.max_tokens), dtype=np.int32),
                "attention_mask": np.zeros((0, self.max_tokens), dtype=np.uint8),
            }
        if self.tokenizer is not None:
            encoded = self.tokenizer(
                texts,
                max_length=self.max_tokens,
                truncation=True,
                padding="max_length",
                return_attention_mask=True,
                return_tensors=None,
            )
            return {
                "input_ids": np.asarray(encoded["input_ids"], dtype=np.int32),
                "attention_mask": np.asarray(encoded["attention_mask"], dtype=np.uint8),
            }
        return _fallback_tokenize(texts, max_tokens=self.max_tokens)


@dataclass(frozen=True, slots=True)
class _TokenizedText:
    input_ids: np.ndarray
    attention_mask: np.ndarray


@dataclass(slots=True)
class CategoryReferenceStore:
    """Dense categorical ids loaded from ClickHouse reference rows.

    The model-facing convention reserves id 0 for missing or unknown values.
    Reference-table rows therefore start at id 1 and can be interpreted as
    sparse one-hot positions through their `one_hot_index`.
    """

    values: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> "CategoryReferenceStore":
        out: dict[tuple[str, str], dict[str, int]] = {}
        for row in rows:
            domain = _normalize_category_value(row.get("domain", ""))
            field_name = _normalize_category_value(row.get("field_name", ""))
            value = _normalize_category_value(row.get("category_value", ""))
            if not domain or not field_name or not value:
                continue
            try:
                category_id = int(row.get("category_id", 0) or 0)
            except Exception:
                category_id = 0
            if category_id <= 0:
                continue
            out.setdefault((domain, field_name), {})[value] = category_id
        return cls(values=out)

    def id(self, domain: str, field_name: str, value: Any) -> int:
        normalized = _normalize_category_value(value)
        if not normalized:
            return 0
        return int(self.values.get((_normalize_category_value(domain), _normalize_category_value(field_name)), {}).get(normalized, 0))

    def count(self) -> int:
        return int(sum(len(values) for values in self.values.values()))


class RollingMarketSampleEngine:
    """Shared rolling event engine for training replay and production serving.

    Historical training appends ClickHouse day blocks into the same ticker queues
    that production uses for live events. The only downstream difference is the
    materialization step: training emits byte chunks for encoder fine-tuning,
    while production gathers cached encoder embeddings for the same chunk
    windows.
    """

    def __init__(self, config: RollingMarketDataConfig) -> None:
        self.config = config
        self.rows_by_ticker: dict[str, np.ndarray] = {}
        self._processed_offsets: dict[str, int] = {}
        self.macro_bars = MacroBarFrame(rows=[])
        q_live_context_configs = {
            "ticker_news": ExternalAsOfContextConfig(name="ticker_news", timestamp_column="timestamp_us", max_items=config.news_max_items * config.news_token_chunks),
            "market_news": ExternalAsOfContextConfig(name="market_news", timestamp_column="timestamp_us", max_items=config.market_news_max_items * config.market_news_token_chunks),
            "sec_filings": ExternalAsOfContextConfig(name="sec_filings", timestamp_column="timestamp_us", max_items=config.sec_max_items * config.sec_token_chunks),
            "xbrl": ExternalAsOfContextConfig(name="xbrl", timestamp_column="timestamp_us", max_items=config.xbrl_max_items),
        }
        enabled_q_live = {
            name: q_live_context_configs[name]
            for name in config.q_live_contexts
            if name in q_live_context_configs
        }
        self.external_contexts: dict[str, ExternalAsOfStore] = {
            **{name: ExternalAsOfStore(source) for name, source in enabled_q_live.items()},
            **{
                source.name: ExternalAsOfStore(source)
                for source in config.external_contexts
            },
        }
        self._text_tokenizer = _TextTokenizerAdapter(config)
        self._text_token_cache: dict[tuple[str, str], _TokenizedText] = {}
        self.category_references = CategoryReferenceStore()

    @property
    def context_lags(self) -> tuple[int, ...]:
        return self.config.context_lags

    def append_rows_by_ticker(self, rows_by_ticker: Mapping[str, np.ndarray]) -> None:
        for ticker_raw, rows in rows_by_ticker.items():
            ticker = str(ticker_raw).upper()
            if rows.size == 0:
                continue
            if rows.dtype != EVENT_ROW_DTYPE:
                normalized = np.zeros((rows.shape[0],), dtype=EVENT_ROW_DTYPE)
                for name in EVENT_ROW_DTYPE.names or ():
                    if name in rows.dtype.names:
                        normalized[name] = rows[name]
                rows = normalized
            current = self.rows_by_ticker.get(ticker)
            merged = rows.copy() if current is None or current.size == 0 else np.concatenate([current, rows])
            order = np.argsort(merged["ordinal"], kind="mergesort")
            merged = merged[order]
            if merged.shape[0] > 1:
                _, unique_last = np.unique(merged["ordinal"][::-1], return_index=True)
                keep = merged.shape[0] - 1 - unique_last
                merged = merged[np.sort(keep)]
            self.rows_by_ticker[ticker] = merged

    def append_compact_events(self, events: Iterable[CompactEvent]) -> None:
        grouped: dict[str, list[CompactEvent]] = {}
        for event in events:
            grouped.setdefault(event.ticker.upper(), []).append(event)
        rows_by_ticker: dict[str, np.ndarray] = {}
        for ticker, ticker_events in grouped.items():
            normalized: list[CompactEvent] = []
            next_ordinal = int(self.rows_by_ticker.get(ticker, np.zeros((0,), dtype=EVENT_ROW_DTYPE))["ordinal"][-1]) + 1 if ticker in self.rows_by_ticker and self.rows_by_ticker[ticker].size else 0
            for event in sorted(ticker_events, key=lambda item: item.sort_key):
                if event.ordinal is None:
                    normalized.append(
                        CompactEvent(
                            ticker=event.ticker,
                            sip_timestamp_us=event.sip_timestamp_us,
                            event_type=event.event_type,
                            price_primary_int=event.price_primary_int,
                            price_secondary_int=event.price_secondary_int,
                            size_primary=event.size_primary,
                            size_secondary=event.size_secondary,
                            exchange_primary=event.exchange_primary,
                            exchange_secondary=event.exchange_secondary,
                            event_flags=event.event_flags,
                            conditions_packed=event.conditions_packed,
                            source_sequence=event.source_sequence,
                            arrival_sequence=event.arrival_sequence,
                            ordinal=next_ordinal,
                            issue_flags=event.issue_flags,
                        )
                    )
                    next_ordinal += 1
                else:
                    normalized.append(event)
                    next_ordinal = max(next_ordinal, int(event.ordinal) + 1)
            rows_by_ticker[ticker] = events_to_rows(normalized)
        self.append_rows_by_ticker(rows_by_ticker)

    def load_macro_bars(self, bars: MacroBarFrame) -> None:
        self.macro_bars = bars

    def load_external_context(self, name: str, rows: Iterable[Mapping[str, Any]]) -> None:
        store = self.external_contexts.get(name)
        if store is None:
            raise KeyError(f"Unknown external context source: {name}")
        store.add_rows(rows)

    def load_external_contexts(self, rows_by_context: Mapping[str, Iterable[Mapping[str, Any]]]) -> None:
        for name, rows in rows_by_context.items():
            self.load_external_context(name, rows)

    def load_category_references(self, rows: Iterable[Mapping[str, Any]] | CategoryReferenceStore) -> None:
        if isinstance(rows, CategoryReferenceStore):
            self.category_references = rows
        else:
            self.category_references = CategoryReferenceStore.from_rows(rows)

    def build_ready_index_blocks(self, *, max_samples: int = 0) -> tuple[RollingReadyIndexBlock, ...]:
        """Return lightweight ready-origin arrays without allocating windows.

        This is the fast path for training/profiling. It keeps one NumPy array
        of origin offsets per ticker and postpones `ChunkWindowIndex` creation
        until a concrete batch is materialized.
        """

        context = int(self.config.events_per_chunk)
        lags = self.context_lags
        if not lags:
            return ()
        min_origin_offset = int(self.config.max_context_lag) + context - 1
        stride = max(1, int(self.config.sample_stride_events))
        cap = int(max_samples or self.config.max_ready_samples)
        eligible: list[tuple[str, np.ndarray, int, int]] = []
        for ticker in sorted(self.rows_by_ticker):
            rows = self.rows_by_ticker[ticker]
            if rows.shape[0] <= min_origin_offset:
                continue
            start_offset = max(min_origin_offset, self._processed_offsets.get(ticker, min_origin_offset))
            if start_offset >= rows.shape[0]:
                continue
            available = ((int(rows.shape[0]) - int(start_offset) - 1) // stride) + 1
            eligible.append((ticker, rows, start_offset, int(available)))

        if not eligible:
            return ()
        allocations = _allocate_ready_sample_cap([item[3] for item in eligible], cap) if cap > 0 else [item[3] for item in eligible]
        blocks: list[RollingReadyIndexBlock] = []
        for (ticker, rows, start_offset, available), allocation in zip(eligible, allocations, strict=True):
            if allocation <= 0:
                continue
            if cap > 0:
                candidate_count = min(int(available), max(int(allocation), 1))
                origin_offsets = np.empty((0,), dtype=np.int64)
                while True:
                    end_offset = min(int(rows.shape[0]), int(start_offset) + stride * candidate_count)
                    candidates = np.arange(start_offset, end_offset, stride, dtype=np.int64)
                    origin_offsets = _filter_materializable_origins(rows, candidates, lags, context)
                    if origin_offsets.shape[0] >= allocation or candidate_count >= int(available):
                        break
                    candidate_count = min(int(available), max(candidate_count * 2, candidate_count + 1024))
            else:
                origin_offsets = np.arange(start_offset, rows.shape[0], stride, dtype=np.int64)
                origin_offsets = _filter_materializable_origins(rows, origin_offsets, lags, context)
            if origin_offsets.size == 0:
                continue
            if cap > 0 and origin_offsets.shape[0] > allocation:
                origin_offsets = origin_offsets[:allocation]
            blocks.append(RollingReadyIndexBlock(ticker=ticker, rows=rows, origin_offsets=origin_offsets))
        return tuple(blocks)

    def iter_ready_sample_batches(
        self,
        *,
        batch_size: int | None = None,
        max_samples: int = 0,
        blocks: tuple[RollingReadyIndexBlock, ...] | None = None,
    ) -> Iterable[tuple[RollingSampleIndex, ...]]:
        """Yield ready samples while expanding only the current batch."""

        size = int(batch_size or self.config.batch_size)
        if size <= 0:
            raise ValueError("batch_size must be positive")
        ready_blocks = blocks if blocks is not None else self.build_ready_index_blocks(max_samples=max_samples)
        buffer: list[RollingSampleIndex] = []
        for block in ready_blocks:
            offsets = block.origin_offsets
            offset_index = 0
            while offset_index < offsets.shape[0]:
                needed = size - len(buffer)
                take = min(needed, int(offsets.shape[0] - offset_index))
                current = offsets[offset_index : offset_index + take]
                offset_index += take
                buffer.extend(self._sample_indices_from_offsets(block.ticker, block.rows, current))
                if len(buffer) == size:
                    yield tuple(buffer)
                    buffer.clear()
        if buffer:
            yield tuple(buffer)

    def ready_index_count(self, blocks: Iterable[RollingReadyIndexBlock]) -> int:
        return int(sum(block.sample_count for block in blocks))

    def build_ready_indices(self, *, max_samples: int = 0) -> tuple[RollingSampleIndex, ...]:
        blocks = self.build_ready_index_blocks(max_samples=max_samples)
        samples: list[RollingSampleIndex] = []
        for block in blocks:
            samples.extend(self._sample_indices_from_offsets(block.ticker, block.rows, block.origin_offsets))
        return tuple(samples)

    def _sample_indices_from_offsets(
        self,
        ticker: str,
        rows: np.ndarray,
        origin_offsets: np.ndarray,
    ) -> tuple[RollingSampleIndex, ...]:
        context = int(self.config.events_per_chunk)
        lags = self.context_lags
        out: list[RollingSampleIndex] = []
        ordinals = rows["ordinal"]
        timestamps = rows["sip_timestamp_us"]
        for origin_offset in origin_offsets.tolist():
            chunk_windows = []
            for lag in lags:
                chunk_origin_offset = int(origin_offset) - int(lag)
                start = chunk_origin_offset - context + 1
                end = chunk_origin_offset
                chunk_windows.append(
                    ChunkWindowIndex(
                        ticker=ticker,
                        lag_chunks=int(lag),
                        start_ordinal=int(ordinals[start]),
                        end_ordinal=int(ordinals[end]),
                        origin_ordinal=int(ordinals[chunk_origin_offset]),
                        origin_timestamp_us=int(timestamps[chunk_origin_offset]),
                    )
                )
            out.append(
                RollingSampleIndex(
                    ticker=ticker,
                    origin_ordinal=int(ordinals[origin_offset]),
                    origin_timestamp_us=int(timestamps[origin_offset]),
                    chunk_windows=tuple(chunk_windows),
                    macro_asof_timestamp_us=int(timestamps[origin_offset]),
                    global_asof_timestamp_us=int(timestamps[origin_offset]),
                )
            )
        return tuple(out)

    def materialize_training_batch(self, samples: Iterable[RollingSampleIndex], *, batch_id: int = 0) -> RollingTrainingBatch:
        sample_tuple = tuple(samples)
        profiler = DataPrepProfiler("rolling_training_materialize", batch_id=int(batch_id), enabled=True)
        batch = len(sample_tuple)
        context_chunks = len(self.context_lags)
        headers = np.zeros((batch, context_chunks, HEADER_BYTES), dtype=np.uint8)
        events = np.zeros((batch, context_chunks, int(self.config.events_per_chunk), EVENT_BYTES), dtype=np.uint8)
        mask = np.zeros((batch, context_chunks), dtype=np.bool_)
        chunk_origin_ts = np.zeros((batch, context_chunks), dtype=np.int64)
        tickers = np.asarray([sample.ticker for sample in sample_tuple], dtype=object)
        origin_ord = np.asarray([sample.origin_ordinal for sample in sample_tuple], dtype=np.int64)
        origin_ts = np.asarray([sample.origin_timestamp_us for sample in sample_tuple], dtype=np.int64)

        encoded_window_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray] | str] = {}
        cache_hits = 0
        cache_misses = 0
        invalid_reasons: dict[str, int] = {}
        invalid_examples: list[str] = []
        with profiler.stage("encode_compact_windows", count=batch * context_chunks):
            for sample_index, sample in enumerate(sample_tuple):
                rows = self.rows_by_ticker.get(sample.ticker)
                if rows is None:
                    _record_invalid_context(
                        invalid_reasons,
                        invalid_examples,
                        reason="ticker_rows_missing",
                        sample=sample,
                        context_index=-1,
                    )
                    continue
                for context_index, window in enumerate(sample.chunk_windows):
                    cache_key = (sample.ticker, int(window.origin_ordinal))
                    encoded = encoded_window_cache.get(cache_key)
                    if cache_key in encoded_window_cache:
                        cache_hits += 1
                    else:
                        cache_misses += 1
                        bounds = _ordinal_window_bounds(
                            rows,
                            start_ordinal=int(window.start_ordinal),
                            end_ordinal=int(window.end_ordinal),
                            expected_events=int(self.config.events_per_chunk),
                        )
                        if bounds is None:
                            encoded_window_cache[cache_key] = "ordinal_window_not_exact"
                            continue
                        start, end = bounds
                        previous_sip_us = int(rows["sip_timestamp_us"][start - 1]) if start > 0 else None
                        result = encode_unified_event_window(rows[start:end], previous_sip_us=previous_sip_us)
                        encoded = result
                        encoded_window_cache[cache_key] = encoded
                    if isinstance(encoded, str):
                        _record_invalid_context(
                            invalid_reasons,
                            invalid_examples,
                            reason=encoded,
                            sample=sample,
                            context_index=context_index,
                        )
                        continue
                    headers[sample_index, context_index], events[sample_index, context_index] = encoded
                    mask[sample_index, context_index] = True
                    chunk_origin_ts[sample_index, context_index] = int(window.origin_timestamp_us)

        profiler.add_stage("encoded_window_cache_hits", 0.0, count=cache_hits)
        profiler.add_stage("encoded_window_cache_misses", 0.0, count=cache_misses)
        profiler.add_stage("encoded_window_cache_entries", 0.0, count=len(encoded_window_cache))
        if not bool(mask.all()):
            invalid = int(mask.size - mask.sum())
            reason_text = ", ".join(f"{key}={value:,}" for key, value in sorted(invalid_reasons.items())) or "unknown"
            example_text = "; ".join(invalid_examples[:5])
            raise RuntimeError(
                f"Training materialization produced {invalid:,} invalid context chunks; "
                f"ready-index construction must filter bad event windows before batching. "
                f"reasons: {reason_text}. examples: {example_text}"
            )

        with profiler.stage("bar_features", count=batch):
            (
                macro_features,
                global_features,
                macro_bar_timeframes,
                global_bar_timeframes,
                ticker_macro_bars,
                ticker_macro_bar_mask,
                global_market_bars,
                global_market_bar_mask,
            ) = self._materialize_bar_features(sample_tuple)
        with profiler.stage("origin_time_features", count=batch):
            time_features = _origin_time_features(sample_tuple)
            chunk_time_features = _timestamp_feature_arrays(chunk_origin_ts, origin_ts[:, None])
        with profiler.stage("session_features", count=batch):
            session_features = self._materialize_session_features(sample_tuple)
        with profiler.stage("macro_future_labels", count=batch):
            labels, future_macro_bars, future_macro_bar_mask, future_macro_bar_timeframes = self._materialize_future_labels(sample_tuple)
        with profiler.stage("intraday_future_labels", count=batch):
            intraday_labels = self._materialize_intraday_future_labels(sample_tuple)
            labels.update(intraday_labels)
            future_intraday_bars, future_intraday_bar_mask = _intraday_label_bar_tensor(
                intraday_labels,
                horizons=tuple(horizon.name for horizon in self.config.intraday_label_horizons),
            )
        macro_features.update(session_features)
        with profiler.stage("external_context_asof", count=batch * len(self.external_contexts)):
            external = {
                name: [
                    _external_asof_for_sample(name=name, store=store, sample=sample)
                    for sample in sample_tuple
                ]
                for name, store in self.external_contexts.items()
            }
        text_cache_before = len(self._text_token_cache)
        text_items = _count_external_text_items(external, names=("ticker_news", "market_news", "sec_filings"))
        with profiler.stage("text_inputs", count=batch):
            text_inputs = self._materialize_text_inputs(external)
        text_cache_after = len(self._text_token_cache)
        text_misses = max(0, text_cache_after - text_cache_before)
        profiler.add_stage("text_token_cache_entries", 0.0, count=text_cache_after)
        profiler.add_stage("text_token_cache_hits", 0.0, count=max(0, text_items - text_misses))
        profiler.add_stage("text_token_cache_misses", 0.0, count=text_misses)
        with profiler.stage("xbrl_inputs", count=batch):
            xbrl_inputs = self._materialize_xbrl_inputs(sample_tuple, external)
        profile = profiler.finish()
        profile.rows_read = int(sum(len(sample.chunk_windows) * self.config.events_per_chunk for sample in sample_tuple))
        profile.chunks_created = int(mask.sum())
        profile.samples_created = int(batch)
        profile.labels_created = int(sum(value.shape[1] if value.ndim > 1 else 1 for value in labels.values()))
        profile.output_batches_created = 1
        return RollingTrainingBatch(
            headers_uint8=headers,
            events_uint8=events,
            ticker=tickers,
            origin_ordinal=origin_ord,
            origin_timestamp_us=origin_ts,
            time_features=time_features,
            chunk_time_features=chunk_time_features,
            bar_feature_keys=BAR_FEATURE_KEYS,
            future_bar_feature_keys=FUTURE_BAR_FEATURE_KEYS,
            macro_bar_timeframes=macro_bar_timeframes,
            global_bar_symbols=tuple(self.config.global_symbols),
            global_bar_timeframes=global_bar_timeframes,
            future_macro_bar_timeframes=future_macro_bar_timeframes,
            future_intraday_bar_horizons=tuple(horizon.name for horizon in self.config.intraday_label_horizons),
            ticker_macro_bars=ticker_macro_bars,
            ticker_macro_bar_mask=ticker_macro_bar_mask,
            global_market_bars=global_market_bars,
            global_market_bar_mask=global_market_bar_mask,
            future_macro_bars=future_macro_bars,
            future_macro_bar_mask=future_macro_bar_mask,
            future_intraday_bars=future_intraday_bars,
            future_intraday_bar_mask=future_intraday_bar_mask,
            macro_features=macro_features,
            global_features=global_features,
            text_inputs=text_inputs,
            xbrl_inputs=xbrl_inputs,
            external_context=external,
            labels=labels,
            profile=profile,
        )

    def materialize_production_batch(
        self,
        samples: Iterable[RollingSampleIndex],
        embedding_lookup: Mapping[tuple[str, int], np.ndarray],
        *,
        batch_id: int = 0,
    ) -> RollingProductionBatch:
        sample_tuple = tuple(samples)
        profiler = DataPrepProfiler("rolling_production_materialize", batch_id=int(batch_id), enabled=True)
        embedding_dim = _infer_embedding_dim(embedding_lookup)
        market = np.zeros((len(sample_tuple), len(self.context_lags), embedding_dim), dtype=np.float32)
        mask = np.zeros((len(sample_tuple), len(self.context_lags)), dtype=np.bool_)
        with profiler.stage("gather_embedding_context", count=len(sample_tuple) * len(self.context_lags)):
            for sample_index, sample in enumerate(sample_tuple):
                for context_index, window in enumerate(sample.chunk_windows):
                    value = embedding_lookup.get((sample.ticker, int(window.origin_ordinal)))
                    if value is None:
                        continue
                    market[sample_index, context_index] = np.asarray(value, dtype=np.float32)
                    mask[sample_index, context_index] = True
        (
            macro_features,
            global_features,
            macro_bar_timeframes,
            global_bar_timeframes,
            ticker_macro_bars,
            ticker_macro_bar_mask,
            global_market_bars,
            global_market_bar_mask,
        ) = self._materialize_bar_features(sample_tuple)
        time_features = _origin_time_features(sample_tuple)
        external = {
            name: [
                _external_asof_for_sample(name=name, store=store, sample=sample)
                for sample in sample_tuple
            ]
            for name, store in self.external_contexts.items()
        }
        text_inputs = self._materialize_text_inputs(external)
        xbrl_inputs = self._materialize_xbrl_inputs(sample_tuple, external)
        profile = profiler.finish()
        profile.samples_created = len(sample_tuple)
        profile.embeddings_created = int(mask.sum())
        profile.output_batches_created = 1
        return RollingProductionBatch(
            market_embeddings=market,
            market_mask=mask,
            samples=sample_tuple,
            time_features=time_features,
            bar_feature_keys=BAR_FEATURE_KEYS,
            macro_bar_timeframes=macro_bar_timeframes,
            global_bar_symbols=tuple(self.config.global_symbols),
            global_bar_timeframes=global_bar_timeframes,
            ticker_macro_bars=ticker_macro_bars,
            ticker_macro_bar_mask=ticker_macro_bar_mask,
            global_market_bars=global_market_bars,
            global_market_bar_mask=global_market_bar_mask,
            macro_features=macro_features,
            global_features=global_features,
            text_inputs=text_inputs,
            xbrl_inputs=xbrl_inputs,
            external_context=external,
            profile=profile,
        )

    def mark_processed(self, samples: Iterable[RollingSampleIndex]) -> None:
        for sample in samples:
            rows = self.rows_by_ticker.get(sample.ticker)
            if rows is None or rows.size == 0:
                continue
            position = _ordinal_position(rows, int(sample.origin_ordinal))
            if position is None:
                continue
            self._processed_offsets[sample.ticker] = max(self._processed_offsets.get(sample.ticker, 0), int(position) + 1)

    def trim_processed_tails(self) -> None:
        keep_tail = max(0, int(self.config.carryover_events))
        for ticker, rows in list(self.rows_by_ticker.items()):
            processed = self._processed_offsets.get(ticker, 0)
            trim_to = max(0, int(processed) - keep_tail)
            if trim_to <= 0:
                continue
            self.rows_by_ticker[ticker] = rows[trim_to:].copy()
            self._processed_offsets[ticker] = max(0, processed - trim_to)

    def _materialize_bar_features(
        self, samples: tuple[RollingSampleIndex, ...]
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], tuple[str, ...], tuple[str, ...], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        batch = len(samples)
        macro_timeframes = _resolved_macro_timeframes()
        global_timeframes = _resolved_global_timeframes()
        global_symbols = tuple(self.config.global_symbols)
        fields = len(BAR_FEATURE_KEYS)
        ticker_macro_bars = np.zeros((batch, len(macro_timeframes), fields), dtype=np.float32)
        ticker_macro_bar_mask = np.zeros((batch, len(macro_timeframes)), dtype=np.bool_)
        global_market_bars = np.zeros((batch, len(global_symbols), len(global_timeframes), fields), dtype=np.float32)
        global_market_bar_mask = np.zeros((batch, len(global_symbols), len(global_timeframes)), dtype=np.bool_)
        macro_rows: list[dict[str, float]] = []
        global_rows: dict[str, list[dict[str, float]]] = {symbol: [] for symbol in self.config.global_symbols}
        for sample_index, sample in enumerate(samples):
            today_bar, today_valid = self._today_asof_bar(sample.ticker, sample.origin_timestamp_us)
            bars, mask = self.macro_bars.daily_window_tensor(
                symbol=sample.ticker,
                timestamp_us=sample.origin_timestamp_us,
                windows=DAILY_MACRO_WINDOWS,
                today_asof=today_bar,
                today_asof_valid=today_valid,
            )
            ticker_macro_bars[sample_index] = bars
            ticker_macro_bar_mask[sample_index] = mask
            macro_rows.append(_bar_tensor_row(macro_timeframes, bars))
            for symbol_index, symbol in enumerate(global_symbols):
                today_bar, today_valid = self._today_asof_bar(symbol, sample.origin_timestamp_us)
                bars, mask = self.macro_bars.daily_window_tensor(
                    symbol=symbol,
                    timestamp_us=sample.global_asof_timestamp_us,
                    windows=DAILY_GLOBAL_WINDOWS,
                    today_asof=today_bar,
                    today_asof_valid=today_valid,
                )
                global_market_bars[sample_index, symbol_index] = bars
                global_market_bar_mask[sample_index, symbol_index] = mask
                global_rows[symbol].append(_bar_tensor_row(global_timeframes, bars))
        macro = _rows_to_feature_arrays(macro_rows)
        global_features = {f"{symbol}_{key}": value for symbol, rows in global_rows.items() for key, value in _rows_to_feature_arrays(rows).items()}
        return macro, global_features, macro_timeframes, global_timeframes, ticker_macro_bars, ticker_macro_bar_mask, global_market_bars, global_market_bar_mask

    def _materialize_future_labels(self, samples: tuple[RollingSampleIndex, ...]) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, tuple[str, ...]]:
        batch = len(samples)
        label_timeframes = _resolved_label_timeframes()
        bars_out = np.zeros((batch, len(label_timeframes), len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
        mask_out = np.zeros((batch, len(label_timeframes)), dtype=np.bool_)
        rows: list[dict[str, float]] = []
        for sample_index, sample in enumerate(samples):
            bars, mask = self.macro_bars.daily_future_tensor(
                symbol=sample.ticker,
                timestamp_us=sample.origin_timestamp_us,
                windows=DAILY_LABEL_WINDOWS,
            )
            bars_out[sample_index] = _select_future_bar_fields(bars)
            mask_out[sample_index] = mask
            rows.append(_future_bar_tensor_row(label_timeframes, bars_out[sample_index]))
        labels = {f"future_{key}": value for key, value in _rows_to_feature_arrays(rows).items()}
        return labels, bars_out, mask_out, label_timeframes

    def _today_asof_bar(self, ticker: str, origin_timestamp_us: int) -> tuple[np.ndarray, bool]:
        ticker_rows = self.rows_by_ticker.get(str(ticker).upper())
        if ticker_rows is None or ticker_rows.size == 0:
            return np.zeros((len(BAR_FEATURE_KEYS),), dtype=np.float32), False
        timestamps = ticker_rows["sip_timestamp_us"].astype(np.int64, copy=False)
        end = int(np.searchsorted(timestamps, int(origin_timestamp_us), side="right"))
        if end <= 0:
            return np.zeros((len(BAR_FEATURE_KEYS),), dtype=np.float32), False
        day_start_us = _utc_day_start_ms(origin_timestamp_us) * 1000
        start = int(np.searchsorted(timestamps[:end], day_start_us, side="left"))
        if start >= end:
            return np.zeros((len(BAR_FEATURE_KEYS),), dtype=np.float32), False
        return _today_asof_bar_from_events(ticker_rows[start:end]), True

    def _materialize_intraday_future_labels(self, samples: tuple[RollingSampleIndex, ...]) -> dict[str, np.ndarray]:
        """Future same-queue trade bars for short horizons.

        These labels are derived from the current in-memory event queue, so they
        line up with production stream semantics. They intentionally complement
        the macro future labels above, which come from offline daily bars.
        """

        batch = len(samples)
        labels: dict[str, np.ndarray] = {}
        if batch == 0:
            return labels
        for horizon in self.config.intraday_label_horizons:
            prefix = f"future_intraday_bar_{horizon.name}"
            labels[f"{prefix}_has_trade"] = np.zeros((batch,), dtype=np.uint8)
            labels[f"{prefix}_open"] = np.zeros((batch,), dtype=np.float32)
            labels[f"{prefix}_high"] = np.zeros((batch,), dtype=np.float32)
            labels[f"{prefix}_low"] = np.zeros((batch,), dtype=np.float32)
            labels[f"{prefix}_close"] = np.zeros((batch,), dtype=np.float32)
            labels[f"{prefix}_volume"] = np.zeros((batch,), dtype=np.float32)

        by_ticker: dict[str, list[tuple[int, int]]] = {}
        for batch_index, sample in enumerate(samples):
            ticker_rows = self.rows_by_ticker.get(sample.ticker)
            if ticker_rows is None or ticker_rows.size == 0:
                continue
            origin_offset = _ordinal_position(ticker_rows, int(sample.origin_ordinal))
            if origin_offset is not None:
                by_ticker.setdefault(sample.ticker, []).append((batch_index, origin_offset))

        for ticker, indexed_offsets in by_ticker.items():
            ticker_rows = self.rows_by_ticker.get(ticker)
            if ticker_rows is None or ticker_rows.size == 0:
                continue
            batch_indices = np.asarray([item[0] for item in indexed_offsets], dtype=np.int64)
            origin_offsets = np.asarray([item[1] for item in indexed_offsets], dtype=np.int64)
            ticker_labels = build_future_time_bar_labels(
                rows=ticker_rows,
                origin_offsets=origin_offsets,
                horizons=tuple(self.config.intraday_label_horizons),
            )
            for source_key, values in ticker_labels.items():
                target_key = source_key.replace("future_bar_", "future_intraday_bar_", 1)
                if target_key in labels:
                    labels[target_key][batch_indices] = values
        return labels

    def _materialize_session_features(self, samples: tuple[RollingSampleIndex, ...]) -> dict[str, np.ndarray]:
        rows: list[dict[str, float]] = []
        for sample in samples:
            ticker_rows = self.rows_by_ticker.get(sample.ticker)
            if ticker_rows is None or ticker_rows.size == 0:
                rows.append(_empty_session_features())
                continue
            origin_offset = _ordinal_position(ticker_rows, int(sample.origin_ordinal))
            if origin_offset is None:
                rows.append(_empty_session_features())
                continue
            rows.append(_session_features_from_prefix(ticker_rows[: origin_offset + 1]))
        return _rows_to_feature_arrays(rows)

    def _materialize_text_inputs(self, external: Mapping[str, list[list[dict[str, Any]]]]) -> dict[str, dict[str, np.ndarray]]:
        out: dict[str, dict[str, np.ndarray]] = {}
        specs = {
            "ticker_news": (int(self.config.news_max_items), int(self.config.news_token_chunks)),
            "market_news": (int(self.config.market_news_max_items), int(self.config.market_news_token_chunks)),
            "sec_filings": (int(self.config.sec_max_items), int(self.config.sec_token_chunks)),
        }
        for name, (max_items, max_chunks) in specs.items():
            rows_by_sample = external.get(name)
            if rows_by_sample is None:
                continue
            batch = len(rows_by_sample)
            token_width = int(self.config.text_max_tokens)
            input_ids = np.zeros((batch, max_items, max_chunks, token_width), dtype=np.int32)
            attention_mask = np.zeros((batch, max_items, max_chunks, token_width), dtype=np.uint8)
            source_timestamp_us = np.zeros((batch, max_items), dtype=np.int64)
            origin_timestamp_us = np.zeros((batch, max_items), dtype=np.int64)
            item_mask = np.zeros((batch, max_items), dtype=np.bool_)
            chunk_mask = np.zeros((batch, max_items, max_chunks), dtype=np.bool_)
            single_category_ids: dict[str, np.ndarray] = {}
            multi_category_ids: dict[str, np.ndarray] = {}
            multi_category_masks: dict[str, np.ndarray] = {}
            if name in {"ticker_news", "market_news"}:
                single_category_ids["provider_id"] = np.zeros((batch, max_items), dtype=np.uint32)
                single_category_ids["url_domain_id"] = np.zeros((batch, max_items), dtype=np.uint32)
                multi_category_ids["channels_ids"] = np.zeros((batch, max_items, int(self.config.news_max_channels)), dtype=np.uint32)
                multi_category_masks["channels_mask"] = np.zeros((batch, max_items, int(self.config.news_max_channels)), dtype=np.bool_)
                multi_category_ids["provider_tags_ids"] = np.zeros((batch, max_items, int(self.config.news_max_provider_tags)), dtype=np.uint32)
                multi_category_masks["provider_tags_mask"] = np.zeros((batch, max_items, int(self.config.news_max_provider_tags)), dtype=np.bool_)
                multi_category_ids["quality_flags_ids"] = np.zeros((batch, max_items, int(self.config.news_max_quality_flags)), dtype=np.uint32)
                multi_category_masks["quality_flags_mask"] = np.zeros((batch, max_items, int(self.config.news_max_quality_flags)), dtype=np.bool_)
            elif name == "sec_filings":
                single_category_ids["form_id"] = np.zeros((batch, max_items), dtype=np.uint32)
                single_category_ids["text_kind_id"] = np.zeros((batch, max_items), dtype=np.uint32)
                multi_category_ids["quality_flags_ids"] = np.zeros((batch, max_items, int(self.config.sec_max_quality_flags)), dtype=np.uint32)
                multi_category_masks["quality_flags_mask"] = np.zeros((batch, max_items, int(self.config.sec_max_quality_flags)), dtype=np.bool_)
            misses: list[tuple[tuple[str, str], str]] = []
            placements: list[tuple[int, int, tuple[str, str]]] = []
            for sample_index, rows in enumerate(rows_by_sample):
                grouped = _group_text_token_rows(name, rows)
                selected = grouped[-max_items:]
                for item_index, item in enumerate(selected):
                    origin_us = _origin_us_from_external_rows(rows)
                    source_timestamp_us[sample_index, item_index] = int(item["timestamp_us"])
                    origin_timestamp_us[sample_index, item_index] = int(origin_us)
                    item_mask[sample_index, item_index] = True
                    token_rows = item["rows"]
                    row = token_rows[0] if token_rows else item["row"]
                    self._fill_text_metadata_ids(
                        name=name,
                        row=row,
                        sample_index=sample_index,
                        item_index=item_index,
                        single_category_ids=single_category_ids,
                        multi_category_ids=multi_category_ids,
                        multi_category_masks=multi_category_masks,
                    )
                    used_precomputed = False
                    for token_row in token_rows:
                        chunk_index = int(token_row.get("token_chunk_index", 0) or 0)
                        if chunk_index < 0 or chunk_index >= max_chunks:
                            continue
                        row_input_ids = token_row.get("input_ids")
                        row_attention_mask = token_row.get("attention_mask")
                        if isinstance(row_input_ids, list) and isinstance(row_attention_mask, list):
                            length = min(token_width, len(row_input_ids), len(row_attention_mask))
                            if length > 0:
                                input_ids[sample_index, item_index, chunk_index, :length] = np.asarray(row_input_ids[:length], dtype=np.int32)
                                attention_mask[sample_index, item_index, chunk_index, :length] = np.asarray(row_attention_mask[:length], dtype=np.uint8)
                                chunk_mask[sample_index, item_index, chunk_index] = True
                                used_precomputed = True
                    if used_precomputed:
                        continue
                    text = _row_to_model_text(name, row)
                    if not text:
                        continue
                    cache_key = _text_cache_key(name, row, text)
                    placements.append((sample_index, item_index, cache_key))
                    if cache_key not in self._text_token_cache:
                        misses.append((cache_key, text))
            if misses:
                unique_misses: dict[tuple[str, str], str] = {}
                for cache_key, text in misses:
                    unique_misses.setdefault(cache_key, text)
                miss_keys = list(unique_misses)
                encoded = self._text_tokenizer.encode([unique_misses[key] for key in miss_keys])
                for row_index, cache_key in enumerate(miss_keys):
                    self._text_token_cache[cache_key] = _TokenizedText(
                        input_ids=np.asarray(encoded["input_ids"][row_index], dtype=np.int32),
                        attention_mask=np.asarray(encoded["attention_mask"][row_index], dtype=np.uint8),
                    )
            for sample_index, item_index, cache_key in placements:
                tokenized = self._text_token_cache.get(cache_key)
                if tokenized is None:
                    continue
                input_ids[sample_index, item_index, 0] = tokenized.input_ids
                attention_mask[sample_index, item_index, 0] = tokenized.attention_mask
                chunk_mask[sample_index, item_index, 0] = bool(tokenized.attention_mask.any())
            out[name] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "item_mask": item_mask,
                "chunk_mask": chunk_mask,
                **single_category_ids,
                **multi_category_ids,
                **multi_category_masks,
                **_timestamp_feature_arrays(source_timestamp_us, origin_timestamp_us),
            }
        return out

    def _fill_text_metadata_ids(
        self,
        *,
        name: str,
        row: Mapping[str, Any],
        sample_index: int,
        item_index: int,
        single_category_ids: dict[str, np.ndarray],
        multi_category_ids: dict[str, np.ndarray],
        multi_category_masks: dict[str, np.ndarray],
    ) -> None:
        if name in {"ticker_news", "market_news"}:
            single_category_ids["provider_id"][sample_index, item_index] = self.category_references.id("news", "provider", row.get("provider", ""))
            single_category_ids["url_domain_id"][sample_index, item_index] = self.category_references.id("news", "url_domain", row.get("url_domain", ""))
            _fill_multi_category_ids(
                ids=multi_category_ids["channels_ids"],
                mask=multi_category_masks["channels_mask"],
                sample_index=sample_index,
                item_index=item_index,
                values=_split_category_values(row.get("channels", "")),
                store=self.category_references,
                domain="news",
                field_name="channels",
            )
            _fill_multi_category_ids(
                ids=multi_category_ids["provider_tags_ids"],
                mask=multi_category_masks["provider_tags_mask"],
                sample_index=sample_index,
                item_index=item_index,
                values=_split_category_values(row.get("provider_tags", "")),
                store=self.category_references,
                domain="news",
                field_name="provider_tags",
            )
            _fill_multi_category_ids(
                ids=multi_category_ids["quality_flags_ids"],
                mask=multi_category_masks["quality_flags_mask"],
                sample_index=sample_index,
                item_index=item_index,
                values=_split_category_values(row.get("quality_flags", "")),
                store=self.category_references,
                domain="news",
                field_name="quality_flags",
            )
            return
        if name == "sec_filings":
            single_category_ids["form_id"][sample_index, item_index] = self.category_references.id("sec_filings", "form_type", row.get("form_type", ""))
            single_category_ids["text_kind_id"][sample_index, item_index] = self.category_references.id("sec_filings", "text_kind", row.get("text_kind", ""))
            _fill_multi_category_ids(
                ids=multi_category_ids["quality_flags_ids"],
                mask=multi_category_masks["quality_flags_mask"],
                sample_index=sample_index,
                item_index=item_index,
                values=_split_category_values(row.get("quality_flags", "")),
                store=self.category_references,
                domain="sec_filings",
                field_name="quality_flags",
            )

    def _materialize_xbrl_inputs(
        self,
        samples: tuple[RollingSampleIndex, ...],
        external: Mapping[str, list[list[dict[str, Any]]]],
    ) -> dict[str, np.ndarray]:
        rows_by_sample = external.get("xbrl")
        if rows_by_sample is None:
            return {}
        batch = len(rows_by_sample)
        max_items = int(self.config.xbrl_max_items)
        mask = np.zeros((batch, max_items), dtype=np.bool_)
        source_timestamp_us = np.zeros((batch, max_items), dtype=np.int64)
        origin_timestamp_us = np.zeros((batch, max_items), dtype=np.int64)
        value = np.zeros((batch, max_items), dtype=np.float32)
        fiscal_year = np.zeros((batch, max_items), dtype=np.int16)
        age_days = np.zeros((batch, max_items), dtype=np.float32)
        period_end_days = np.zeros((batch, max_items), dtype=np.int32)
        taxonomy_id = np.zeros((batch, max_items), dtype=np.uint32)
        tag_id = np.zeros((batch, max_items), dtype=np.uint32)
        unit_id = np.zeros((batch, max_items), dtype=np.uint32)
        form_id = np.zeros((batch, max_items), dtype=np.uint32)
        row_kind_id = np.zeros((batch, max_items), dtype=np.uint32)
        location_id = np.zeros((batch, max_items), dtype=np.uint32)
        mapping_confidence = np.zeros((batch, max_items), dtype=np.float32)
        for sample_index, rows in enumerate(rows_by_sample):
            selected = list(rows)[-max_items:]
            origin_us = int(samples[sample_index].origin_timestamp_us) if sample_index < len(samples) else 0
            for item_index, row in enumerate(selected):
                row_ts = int(row.get("timestamp_us", 0) or 0)
                mask[sample_index, item_index] = True
                source_timestamp_us[sample_index, item_index] = row_ts
                origin_timestamp_us[sample_index, item_index] = origin_us
                value[sample_index, item_index] = _safe_float32(row.get("value", 0.0))
                fiscal_year[sample_index, item_index] = int(row.get("fiscal_year", 0) or 0)
                age_days[sample_index, item_index] = max(0.0, float(origin_us - row_ts) / 86_400_000_000.0) if origin_us and row_ts else 0.0
                period_end_days[sample_index, item_index] = _date_to_epoch_day(row.get("period_end_date"))
                taxonomy_id[sample_index, item_index] = self.category_references.id("xbrl", "taxonomy", row.get("taxonomy", ""))
                tag_id[sample_index, item_index] = self.category_references.id("xbrl", "tag", row.get("tag", ""))
                unit_id[sample_index, item_index] = self.category_references.id("xbrl", "unit_code", row.get("unit_code", ""))
                form_id[sample_index, item_index] = self.category_references.id("xbrl", "form_type", row.get("form_type", ""))
                row_kind_id[sample_index, item_index] = self.category_references.id("xbrl", "xbrl_row_kind", row.get("xbrl_row_kind", ""))
                location_id[sample_index, item_index] = self.category_references.id("xbrl", "location_code", row.get("location_code", ""))
                mapping_confidence[sample_index, item_index] = _safe_float32(row.get("mapping_confidence_score", 0.0))
        return {
            "mask": mask,
            **_timestamp_feature_arrays(source_timestamp_us, origin_timestamp_us),
            "value": value,
            "fiscal_year": fiscal_year,
            "age_days": age_days,
            "period_end_days": period_end_days,
            "taxonomy_id": taxonomy_id,
            "tag_id": tag_id,
            "unit_id": unit_id,
            "form_id": form_id,
            "row_kind_id": row_kind_id,
            "location_id": location_id,
            "mapping_confidence": mapping_confidence,
        }


class HistoricalClickHouseRollingSource:
    """ClickHouse-backed day source for the rolling engine."""

    def __init__(self, *, config: RollingMarketDataConfig, text_client: ClickHouseHttpClient, bytes_client: PersistentClickHouseBytesClient) -> None:
        self.config = config
        self.text_client = text_client
        self.bytes_client = bytes_client

    def load_tickers_from_index(self, *, limit: int = 0) -> tuple[str, ...]:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.index_table)}"
        limit_sql = f" LIMIT {int(limit)}" if int(limit) > 0 else ""
        query = f"""
SELECT ticker
FROM {table}
ORDER BY ticker
{limit_sql}
FORMAT TSV
"""
        return tuple(line.strip().upper() for line in self.text_client.execute(query).splitlines() if line.strip())

    def fetch_category_references(self) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.category_reference_table)}"
        query = f"""
SELECT
    domain,
    field_name,
    category_value,
    argMax(category_id, updated_at) AS category_id,
    argMax(one_hot_index, updated_at) AS one_hot_index
FROM {table}
GROUP BY
    domain,
    field_name,
    category_value
ORDER BY
    domain,
    field_name,
    category_id
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def fetch_day(self, *, event_date: str, tickers: Iterable[str]) -> HistoricalDayFetchResult:
        ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
        if not ticker_tuple:
            return HistoricalDayFetchResult(rows_by_ticker={}, event_date=event_date, rows_returned=0, fetch_seconds=0.0)
        started = time.perf_counter()
        payload = self.bytes_client.execute_bytes(_day_events_query(self.config, ticker_tuple, event_date=event_date))
        seconds = time.perf_counter() - started
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE)
        out: dict[str, np.ndarray] = {}
        if rows.size:
            span_ids = rows["span_id"]
            boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
            starts = np.concatenate(([0], boundaries))
            ends = np.concatenate((boundaries, [rows.shape[0]]))
            for start, end in zip(starts, ends):
                out[ticker_tuple[int(span_ids[start])]] = rows[start:end].copy()
        return HistoricalDayFetchResult(rows_by_ticker=out, event_date=event_date, rows_returned=int(rows.shape[0]), fetch_seconds=seconds)

    def fetch_macro_bars(
        self,
        *,
        start_date: str,
        end_date: str,
        tickers: Iterable[str],
        include_global: bool = True,
    ) -> MacroBarFrame:
        symbols = {str(ticker).upper() for ticker in tickers if str(ticker).strip()}
        if include_global:
            symbols.update(str(symbol).upper() for symbol in self.config.global_symbols)
        if not symbols:
            return MacroBarFrame(rows=[])
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.macro_bars_table)}"
        symbol_sql = ", ".join(sql_string(symbol) for symbol in sorted(symbols))
        timeframe_sql = ", ".join(sql_string(tf) for tf in self.config.macro_timeframes)
        label_timeframe_sql = ", ".join(sql_string(tf) for tf in sorted(set(self.config.macro_timeframes).union(self.config.label_timeframes)))
        query = f"""
SELECT
    sym,
    timeframe,
    toUnixTimestamp64Milli(bar_start) AS bar_start_ms,
    open,
    high,
    low,
    close,
    volume,
    dollar_volume,
    trade_count,
    quote_count,
    vwap
FROM {table}
WHERE sym IN ({symbol_sql})
  AND timeframe IN ({label_timeframe_sql or timeframe_sql})
  AND bar_start >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC') - INTERVAL {int(self.config.macro_lookback_days)} DAY
  AND bar_start < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC') + INTERVAL {int(self.config.label_lookahead_days)} DAY
ORDER BY sym, timeframe, bar_start
FORMAT JSONEachRow
"""
        started = time.perf_counter()
        text = self.text_client.execute(query)
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return MacroBarFrame(rows=rows, fetch_seconds=time.perf_counter() - started)

    def fetch_external_contexts(
        self,
        *,
        start_timestamp_us: int,
        end_timestamp_us: int,
        tickers: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        ticker_tuple = tuple(sorted({str(ticker).upper() for ticker in tickers if str(ticker).strip()}))
        if not ticker_tuple:
            return {}
        ticker_sql = ", ".join(sql_string(ticker) for ticker in ticker_tuple)
        out: dict[str, list[dict[str, Any]]] = {}
        for source in self.config.external_contexts:
            if not source.table:
                continue
            table = source.table if "." in source.table else f"{quote_ident(self.config.database)}.{quote_ident(source.table)}"
            columns = [source.ticker_column, source.timestamp_column, source.id_column, *source.payload_columns]
            column_sql = ", ".join(quote_ident(column) for column in dict.fromkeys(columns))
            query = f"""
SELECT {column_sql}
FROM {table}
WHERE {quote_ident(source.ticker_column)} IN ({ticker_sql})
  AND {quote_ident(source.timestamp_column)} >= {_timestamp_us_to_source_unit(start_timestamp_us, source)}
  AND {quote_ident(source.timestamp_column)} <= {_timestamp_us_to_source_unit(end_timestamp_us, source)}
ORDER BY {quote_ident(source.ticker_column)}, {quote_ident(source.timestamp_column)}
FORMAT JSONEachRow
"""
            text = self.text_client.execute(query)
            out[source.name] = [json.loads(line) for line in text.splitlines() if line.strip()]
        return out

    def fetch_q_live_contexts(
        self,
        *,
        start_timestamp_us: int,
        end_timestamp_us: int,
        tickers: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        ticker_tuple = tuple(sorted({str(ticker).upper() for ticker in tickers if str(ticker).strip()}))
        if not ticker_tuple:
            return {}
        enabled = set(self.config.q_live_contexts)
        out: dict[str, list[dict[str, Any]]] = {}
        if "ticker_news" in enabled:
            out["ticker_news"] = self._fetch_q_live_news(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        if "market_news" in enabled:
            out["market_news"] = self._fetch_market_news(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
            )
        if "sec_filings" in enabled:
            out["sec_filings"] = self._fetch_sec_filing_context(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        if "xbrl" in enabled:
            out["xbrl"] = self._fetch_sec_xbrl_context(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        out.update(self.fetch_external_contexts(start_timestamp_us=start_timestamp_us, end_timestamp_us=end_timestamp_us, tickers=ticker_tuple))
        return out

    def _fetch_q_live_news(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.database)
        table = f"{database}.{quote_ident(self.config.news_token_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_expr = _date_time64_from_us(max(0, int(start_timestamp_us) - int(self.config.news_lookback_days) * 86_400_000_000))
        end_expr = _date_time64_from_us(int(end_timestamp_us))
        row_limit = int(self.config.news_max_items) * int(self.config.news_token_chunks)
        query = f"""
SELECT
    ticker,
    timestamp_us,
    source_id,
    provider,
    provider_article_id,
    title,
    article_url,
    url_domain,
    channels,
    provider_tags,
    quality_flags,
    tokenizer_model,
    max_tokens,
    token_chunk_index,
    token_start,
    token_end,
    original_token_count,
    token_count,
    padding_tokens,
    was_truncated,
    input_ids,
    attention_mask,
    text_hash,
    text_char_count,
    source_text_char_count,
    text_prefix_truncated
FROM {table}
PREWHERE ticker IN ({ticker_sql})
WHERE published_at_utc >= {start_expr}
  AND published_at_utc <= {end_expr}
ORDER BY ticker, published_at_utc DESC, source_id, token_chunk_index
LIMIT {row_limit} BY ticker
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_market_news(self, *, start_timestamp_us: int, end_timestamp_us: int) -> list[dict[str, Any]]:
        database = quote_ident(self.config.database)
        table = f"{database}.{quote_ident(self.config.news_token_table)}"
        start_expr = _date_time64_from_us(max(0, int(start_timestamp_us) - int(self.config.news_lookback_days) * 86_400_000_000))
        end_expr = _date_time64_from_us(int(end_timestamp_us))
        max_items = int(self.config.market_news_max_items)
        max_chunks = int(self.config.market_news_token_chunks)
        query = f"""
WITH latest_sources AS
(
    SELECT
        source_id,
        max(timestamp_us) AS latest_timestamp_us
    FROM {table}
    WHERE published_at_utc >= {start_expr}
      AND published_at_utc <= {end_expr}
    GROUP BY source_id
    ORDER BY latest_timestamp_us DESC
    LIMIT {max_items}
)
SELECT
    '__MARKET__' AS ticker,
    timestamp_us,
    source_id,
    provider,
    provider_article_id,
    title,
    article_url,
    url_domain,
    channels,
    provider_tags,
    quality_flags,
    tokenizer_model,
    max_tokens,
    token_chunk_index,
    token_start,
    token_end,
    original_token_count,
    token_count,
    padding_tokens,
    was_truncated,
    input_ids,
    attention_mask,
    text_hash,
    text_char_count,
    source_text_char_count,
    text_prefix_truncated
FROM
(
    SELECT t.*
    FROM {table} AS t
    INNER JOIN latest_sources AS s ON t.source_id = s.source_id
    WHERE t.token_chunk_index < {max_chunks}
    ORDER BY t.source_id, t.token_chunk_index, t.ticker
    LIMIT 1 BY source_id, token_chunk_index
)
ORDER BY timestamp_us, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_sec_filing_context(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.sec_context_database)
        table = f"{database}.{quote_ident(self.config.sec_filing_text_token_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_us = max(0, int(start_timestamp_us) - int(self.config.sec_lookback_days) * 86_400_000_000)
        end_us = int(end_timestamp_us)
        row_limit = int(self.config.sec_max_items) * int(self.config.sec_token_chunks)
        query = f"""
SELECT
    ticker,
    timestamp_us,
    source_id,
    accession_number,
    cik,
    form_type,
    text_rank,
    document_id,
    text_kind,
    quality_flags,
    tokenizer_model,
    max_tokens,
    token_chunk_index,
    token_start,
    token_end,
    original_token_count,
    token_count,
    padding_tokens,
    was_truncated,
    input_ids,
    attention_mask,
    text_hash,
    text_char_count,
    source_text_char_count,
    text_prefix_truncated
FROM {table}
PREWHERE ticker IN ({ticker_sql})
WHERE timestamp_us >= {start_us}
  AND timestamp_us <= {end_us}
ORDER BY ticker, timestamp_us DESC, accession_number, text_rank, document_id
LIMIT {row_limit} BY ticker
FORMAT JSONEachRow
"""
        rows = [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]
        rows.sort(key=lambda row: (str(row.get("ticker", "")), int(row.get("timestamp_us", 0)), str(row.get("accession_number", "")), int(row.get("text_rank", 0) or 0)))
        return rows

    def _attach_q_live_sec_text(self, *, database: str, filings: list[dict[str, Any]]) -> None:
        pairs = sorted({(str(row.get("cik", "")), str(row.get("accession_number", ""))) for row in filings if row.get("cik") and row.get("accession_number")})
        if not pairs:
            return
        pair_sql = ", ".join(f"({sql_string(cik)}, {sql_string(accession)})" for cik, accession in pairs)
        query = f"""
SELECT
    cik,
    accession_number,
    document_id,
    text_kind,
    substring(text, 1, 16000) AS text,
    text_char_count,
    quality_flags
FROM {database}.sec_filing_text_v2 FINAL
PREWHERE (cik, accession_number) IN ({pair_sql})
ORDER BY cik, accession_number, text_kind, document_id
LIMIT 2 BY cik, accession_number
FORMAT JSONEachRow
"""
        text_rows = [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]
        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in text_rows:
            by_pair.setdefault((str(row.get("cik", "")), str(row.get("accession_number", ""))), []).append(row)
        for row in filings:
            row["texts"] = by_pair.get((str(row.get("cik", "")), str(row.get("accession_number", ""))), [])

    def _fetch_sec_xbrl_context(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.sec_context_database)
        table = f"{database}.{quote_ident(self.config.sec_xbrl_context_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_us = max(0, int(start_timestamp_us) - int(self.config.xbrl_lookback_days) * 86_400_000_000)
        end_us = int(end_timestamp_us)
        query = f"""
SELECT
    ticker,
    timestamp_us,
    source_id,
    cik,
    issuer_id,
    taxonomy,
    tag,
    unit_code,
    fiscal_year,
    fiscal_period,
    form_type,
    accepted_at_source,
    accession_number,
    period_end_date,
    value,
    calendar_period_code,
    location_code,
    xbrl_row_kind,
    bridge_id,
    mapping_confidence AS mapping_confidence_score
FROM {table}
PREWHERE ticker IN ({ticker_sql})
WHERE timestamp_us >= {start_us}
  AND timestamp_us <= {end_us}
ORDER BY ticker, timestamp_us DESC, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
LIMIT {int(self.config.xbrl_max_items)} BY ticker
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]


def _day_events_query(config: RollingMarketDataConfig, tickers: tuple[str, ...], *, event_date: str) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    parts = []
    for index, ticker in enumerate(tickers):
        parts.append(
            f"""
SELECT
    toUInt32({index}) AS span_id,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM {table}
PREWHERE event_date = toDate({sql_string(event_date)})
  AND ticker = {sql_string(ticker)}
""".strip()
        )
    return f"""
SELECT
    span_id,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM
(
{" UNION ALL ".join(parts)}
)
ORDER BY span_id, ordinal
{_query_settings(config)}
FORMAT RowBinary
"""


def _query_settings(config: RollingMarketDataConfig) -> str:
    settings: list[str] = []
    if int(config.max_threads) > 0:
        settings.append(f"max_threads = {int(config.max_threads)}")
    if str(config.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def _filter_materializable_origins(rows: np.ndarray, origin_offsets: np.ndarray, lags: tuple[int, ...], context: int) -> np.ndarray:
    if origin_offsets.size == 0:
        return origin_offsets
    valid = np.ones((origin_offsets.shape[0],), dtype=np.bool_)
    chunk_cache: dict[int, bool] = {}
    for index, origin_offset in enumerate(origin_offsets.tolist()):
        for lag in lags:
            chunk_origin_offset = int(origin_offset) - int(lag)
            cached = chunk_cache.get(chunk_origin_offset)
            if cached is None:
                cached = _is_materializable_chunk_origin(rows, chunk_origin_offset, context)
                chunk_cache[chunk_origin_offset] = cached
            if not cached:
                valid[index] = False
                break
    return origin_offsets[valid]


def _is_materializable_chunk_origin(rows: np.ndarray, origin_offset: int, context: int) -> bool:
    end = int(origin_offset)
    start = end - int(context) + 1
    if start < 0 or end >= int(rows.shape[0]):
        return False
    window = rows[start : end + 1]
    ordinals = window["ordinal"].astype(np.int64, copy=False)
    if ordinals.shape[0] != int(context) or not bool(np.all(ordinals[1:] == ordinals[:-1] + 1)):
        return False
    return _event_window_rejection_reason(window) is None


def _event_window_rejection_reason(rows: np.ndarray) -> str | None:
    if rows.shape[0] != DEFAULT_CONTEXT_EVENTS:
        return "invalid_window_size"
    event_types = rows["event_type"].astype(np.uint8, copy=False)
    quote_mask = event_types == QUOTE_EVENT_TYPE
    quote_positions = np.flatnonzero(quote_mask)
    if quote_positions.size == 0:
        return "no_quote_anchor"
    anchor_idx = int(quote_positions[-1])
    primary_prices = decode_price_array(rows["price_primary_int"], rows["event_flags"] & 1)
    secondary_prices = decode_price_array(rows["price_secondary_int"], (rows["event_flags"] >> 1) & 1)
    anchor_ask = float(primary_prices[anchor_idx])
    anchor_bid = float(secondary_prices[anchor_idx])
    if anchor_ask <= 0.0 or anchor_bid <= 0.0 or anchor_ask < anchor_bid:
        return "invalid_quote_anchor"
    tick_size = 0.01 if anchor_ask >= 1.0 else 0.0001
    ask_anchor_ticks = int(round(anchor_ask / tick_size))
    spread_anchor_ticks = int(round((anchor_ask - anchor_bid) / tick_size))
    if ask_anchor_ticks >= 2**20:
        return "ask_anchor_overflow"
    if spread_anchor_ticks >= 2**16:
        return "spread_anchor_overflow"
    quote_count = int(np.count_nonzero(quote_mask))
    trade_mask = event_types == TRADE_EVENT_TYPE
    trade_count = int(np.count_nonzero(trade_mask))
    if quote_count > 255 or trade_count > 255:
        return "event_count_overflow"

    price_1 = np.zeros((rows.shape[0],), dtype=np.int64)
    price_2 = np.zeros((rows.shape[0],), dtype=np.int64)
    ask = primary_prices[quote_mask]
    bid = secondary_prices[quote_mask]
    if np.any((ask <= 0.0) | (bid <= 0.0) | (ask < bid)):
        return "invalid_quote_event"
    ask_ticks = np.rint(ask / tick_size).astype(np.int64)
    spread_ticks = np.rint((ask - bid) / tick_size).astype(np.int64)
    price_1[quote_mask] = ask_ticks - ask_anchor_ticks
    price_2[quote_mask] = spread_ticks - spread_anchor_ticks
    if np.any(trade_mask):
        trade_price = primary_prices[trade_mask]
        if np.any(trade_price <= 0.0):
            return "invalid_trade_event"
        trade_ticks = np.rint(trade_price / tick_size).astype(np.int64)
        price_1[trade_mask] = trade_ticks - ask_anchor_ticks
    if np.any((price_1 < -32768) | (price_1 > 32767) | (price_2 < -32768) | (price_2 > 32767)):
        return "price_delta_overflow"
    return None


def _record_invalid_context(
    reasons: dict[str, int],
    examples: list[str],
    *,
    reason: str,
    sample: RollingSampleIndex,
    context_index: int,
) -> None:
    reasons[reason] = int(reasons.get(reason, 0)) + 1
    if len(examples) >= 8:
        return
    window_text = ""
    if 0 <= int(context_index) < len(sample.chunk_windows):
        window = sample.chunk_windows[int(context_index)]
        window_text = f" window={int(window.start_ordinal)}-{int(window.end_ordinal)} lag={int(window.lag_chunks)}"
    examples.append(
        f"ticker={sample.ticker} origin={int(sample.origin_ordinal)} "
        f"context={int(context_index)} reason={reason}{window_text}"
    )


def _ordinal_position(rows: np.ndarray, ordinal: int) -> int | None:
    ordinals = rows["ordinal"].astype(np.int64, copy=False)
    position = int(np.searchsorted(ordinals, int(ordinal), side="left"))
    if position >= int(ordinals.shape[0]) or int(ordinals[position]) != int(ordinal):
        return None
    return position


def _ordinal_window_bounds(
    rows: np.ndarray,
    *,
    start_ordinal: int,
    end_ordinal: int,
    expected_events: int,
) -> tuple[int, int] | None:
    if int(expected_events) <= 0:
        return None
    start = _ordinal_position(rows, int(start_ordinal))
    end_position = _ordinal_position(rows, int(end_ordinal))
    if start is None or end_position is None:
        return None
    end = int(end_position) + 1
    if end - int(start) != int(expected_events):
        return None
    ordinals = rows["ordinal"].astype(np.int64, copy=False)
    if int(ordinals[end_position]) - int(ordinals[start]) != int(expected_events) - 1:
        return None
    return int(start), end


def _allocate_ready_sample_cap(available_counts: list[int], cap: int) -> list[int]:
    allocations = [0 for _count in available_counts]
    remaining = max(0, int(cap))
    active = [index for index, count in enumerate(available_counts) if int(count) > 0]
    while active and remaining > 0:
        quota = max(1, remaining // len(active))
        next_active: list[int] = []
        for index in active:
            available = max(0, int(available_counts[index]) - int(allocations[index]))
            take = min(available, quota, remaining)
            allocations[index] += int(take)
            remaining -= int(take)
            if allocations[index] < int(available_counts[index]):
                next_active.append(index)
            if remaining <= 0:
                break
        active = next_active
    return allocations


def _rows_to_feature_arrays(rows: list[dict[str, float]]) -> dict[str, np.ndarray]:
    if not rows:
        return {}
    keys = sorted({key for row in rows for key in row})
    return {key: np.asarray([float(row.get(key, 0.0) or 0.0) for row in rows], dtype=np.float32) for key in keys}


def _fallback_tokenize(texts: list[str], *, max_tokens: int) -> dict[str, np.ndarray]:
    input_ids = np.zeros((len(texts), int(max_tokens)), dtype=np.int32)
    attention_mask = np.zeros((len(texts), int(max_tokens)), dtype=np.uint8)
    for row_index, text in enumerate(texts):
        tokens = re.findall(r"\w+|[^\w\s]", str(text).lower(), flags=re.UNICODE)[: int(max_tokens)]
        for token_index, token in enumerate(tokens):
            input_ids[row_index, token_index] = int(_stable_uint32(token) % 151_936) + 1
            attention_mask[row_index, token_index] = 1
    return {"input_ids": input_ids, "attention_mask": attention_mask}


def _group_text_token_rows(name: str, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        source_id = str(row.get("source_id", "") or row.get("id", "") or row.get("accession_number", ""))
        timestamp_us = str(row.get("timestamp_us", "") or row.get("timestamp_ns", "") or "")
        if not source_id:
            text = _row_to_model_text(name, row)
            source_id = f"text:{_stable_uint32(text)}:{len(text)}"
        key = (str(row.get("ticker", "")).upper(), timestamp_us, source_id)
        item = grouped.setdefault(
            key,
            {
                "ticker": key[0],
                "timestamp_us": int(row.get("timestamp_us", 0) or 0),
                "source_id": source_id,
                "rows": [],
                "row": dict(row),
            },
        )
        item["rows"].append(dict(row))
    out = list(grouped.values())
    for item in out:
        item["rows"].sort(key=lambda row: int(row.get("token_chunk_index", 0) or 0))
    out.sort(key=lambda item: (int(item["timestamp_us"]), str(item["source_id"])))
    return out


def _external_lookup_ticker(name: str, ticker: str) -> str:
    return "__MARKET__" if name == "market_news" else str(ticker).upper()


def _external_asof_for_sample(*, name: str, store: ExternalAsOfStore, sample: RollingSampleIndex) -> list[dict[str, Any]]:
    rows = store.asof(ticker=_external_lookup_ticker(name, sample.ticker), timestamp_us=sample.origin_timestamp_us)
    origin_us = int(sample.origin_timestamp_us)
    out: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        copied["_sample_origin_timestamp_us"] = origin_us
        out.append(copied)
    return out


def _origin_us_from_external_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    for row in rows:
        value = row.get("_sample_origin_timestamp_us")
        if value is not None:
            return int(value)
    return 0


def _origin_time_features(samples: tuple[RollingSampleIndex, ...]) -> dict[str, np.ndarray]:
    """Convert the single absolute sample origin timestamp into model features."""

    timestamps = np.asarray([int(sample.origin_timestamp_us) for sample in samples], dtype=np.int64)
    return _timestamp_feature_arrays(timestamps, timestamps)


def _timestamp_feature_arrays(timestamps_us: np.ndarray, origins_us: np.ndarray) -> dict[str, np.ndarray]:
    """Return the unified model-facing representation for timestamp arrays.

    `timestamps_us` is the source timestamp being represented. `origins_us` is
    broadcast to the same shape and anchors all relative features. Absolute
    source timestamps are not returned.
    """

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
    second_of_day = np.zeros(source.shape, dtype=np.float32)
    day_of_week = np.zeros(source.shape, dtype=np.float32)
    day_of_year = np.zeros(source.shape, dtype=np.float32)
    years_since_2000 = np.zeros(source.shape, dtype=np.float32)
    epoch_2000 = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    for flat_index, value in enumerate(source.ravel()):
        if int(value) <= 0:
            continue
        timestamp = dt.datetime.fromtimestamp(int(value) / 1_000_000.0, tz=dt.timezone.utc)
        out_index = np.unravel_index(flat_index, source.shape)
        second_of_day[out_index] = np.float32(
            timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second + timestamp.microsecond / 1_000_000.0
        )
        day_of_week[out_index] = np.float32(timestamp.weekday())
        day_of_year[out_index] = np.float32(timestamp.timetuple().tm_yday - 1)
        years_since_2000[out_index] = np.float32((timestamp - epoch_2000).total_seconds() / (365.2425 * 86_400.0))
    return {
        "time_delta_seconds": delta_seconds,
        "time_delta_seconds_log1p_signed": delta_seconds_log1p_signed,
        "time_age_seconds_log1p": age_seconds_log1p,
        "time_utc_second_of_day_sin": np.sin(2.0 * np.pi * second_of_day / 86_400.0).astype(np.float32) * valid,
        "time_utc_second_of_day_cos": np.cos(2.0 * np.pi * second_of_day / 86_400.0).astype(np.float32) * valid,
        "time_utc_day_of_week_sin": np.sin(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
        "time_utc_day_of_week_cos": np.cos(2.0 * np.pi * day_of_week / 7.0).astype(np.float32) * valid,
        "time_utc_day_of_year_sin": np.sin(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
        "time_utc_day_of_year_cos": np.cos(2.0 * np.pi * day_of_year / 366.0).astype(np.float32) * valid,
        "time_years_since_2000": years_since_2000,
    }


def _row_to_model_text(name: str, row: Mapping[str, Any]) -> str:
    if name in {"ticker_news", "market_news"}:
        parts = [
            str(row.get("title", "") or ""),
            str(row.get("teaser", "") or ""),
            str(row.get("text", "") or ""),
        ]
        return "\n".join(part for part in parts if part).strip()
    if name == "sec_filings":
        heading = " ".join(
            str(row.get(key, "") or "")
            for key in ("form_type", "company_name", "items", "primary_document")
            if row.get(key)
        )
        direct_text = str(row.get("text", "") or "")
        direct_kind = str(row.get("text_kind", "") or "")
        texts = row.get("texts") or []
        body_parts: list[str] = []
        if direct_text:
            body_parts.append(f"{direct_kind}\n{direct_text}" if direct_kind else direct_text)
        if isinstance(texts, list):
            for text_row in texts:
                if isinstance(text_row, Mapping):
                    kind = str(text_row.get("text_kind", "") or "")
                    body = str(text_row.get("text", "") or "")
                    body_parts.append(f"{kind}\n{body}" if kind else body)
        return "\n".join(part for part in [heading, *body_parts] if part).strip()
    payload = row.get("text") or row.get("headline") or row.get("title") or ""
    return str(payload)


def _text_cache_key(name: str, row: Mapping[str, Any], text: str) -> tuple[str, str]:
    source_id = str(row.get("source_id", "") or row.get("id", "") or row.get("accession_number", "") or "")
    timestamp = str(row.get("timestamp_us", "") or row.get("timestamp_ns", "") or "")
    if source_id:
        return (name, f"{source_id}:{timestamp}")
    return (name, f"text:{_stable_uint32(text)}:{len(text)}")


def _count_external_text_items(external: Mapping[str, list[list[dict[str, Any]]]], *, names: tuple[str, ...]) -> int:
    count = 0
    for name in names:
        for rows in external.get(name, []):
            grouped = _group_text_token_rows(name, rows)
            if grouped:
                count += len(grouped)
                continue
            for row in rows:
                if _row_to_model_text(name, row):
                    count += 1
    return count


def _stable_uint32(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    digest = hashlib.blake2b(text.encode("utf-8", errors="ignore"), digest_size=4).digest()
    return int.from_bytes(digest, "little", signed=False)


def _normalize_category_value(value: Any) -> str:
    return str(value or "").strip()


def _split_category_values(value: Any) -> tuple[str, ...]:
    text = str(value or "")
    if not text:
        return ()
    return tuple(item for item in (_normalize_category_value(part) for part in text.split(",")) if item)


def _fill_multi_category_ids(
    *,
    ids: np.ndarray,
    mask: np.ndarray,
    sample_index: int,
    item_index: int,
    values: Iterable[str],
    store: CategoryReferenceStore,
    domain: str,
    field_name: str,
) -> None:
    max_values = int(ids.shape[-1])
    out_index = 0
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_category_value(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        category_id = store.id(domain, field_name, normalized)
        if category_id <= 0:
            continue
        ids[sample_index, item_index, out_index] = category_id
        mask[sample_index, item_index, out_index] = True
        out_index += 1
        if out_index >= max_values:
            break


def _safe_float32(value: Any) -> np.float32:
    try:
        parsed = float(value)
    except Exception:
        parsed = 0.0
    if not np.isfinite(parsed):
        parsed = 0.0
    return np.float32(parsed)


def _date_to_epoch_day(value: Any) -> int:
    if value is None:
        return 0
    text = str(value)
    if not text or text.startswith("0000-"):
        return 0
    try:
        return (dt.date.fromisoformat(text[:10]) - dt.date(1970, 1, 1)).days
    except Exception:
        return 0


def _resolved_macro_timeframes() -> tuple[str, ...]:
    return tuple(name for name, _count in DAILY_MACRO_WINDOWS)


def _resolved_global_timeframes() -> tuple[str, ...]:
    return tuple(name for name, _count in DAILY_GLOBAL_WINDOWS)


def _resolved_label_timeframes() -> tuple[str, ...]:
    return tuple(name for name, _count in DAILY_LABEL_WINDOWS)


def max_daily_macro_lookback_days() -> int:
    return max(count for _name, count in DAILY_MACRO_WINDOWS)


def max_daily_label_lookahead_days() -> int:
    return max(count for _name, count in DAILY_LABEL_WINDOWS)


def _utc_day_start_ms(timestamp_us: int) -> int:
    day = dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).date()
    return int(dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _aggregate_daily_bar_arrays(arrays: Mapping[str, np.ndarray], *, start: int, end: int) -> np.ndarray:
    out = np.zeros((len(BAR_FEATURE_KEYS),), dtype=np.float32)
    if end <= start:
        return out
    open_values = arrays.get("open")
    close_values = arrays.get("close")
    high_values = arrays.get("high")
    low_values = arrays.get("low")
    volume_values = arrays.get("volume")
    dollar_values = arrays.get("dollar_volume")
    trade_count_values = arrays.get("trade_count")
    quote_count_values = arrays.get("quote_count")
    vwap_values = arrays.get("vwap")
    volume = float(np.sum(volume_values[start:end])) if volume_values is not None else 0.0
    dollar_volume = float(np.sum(dollar_values[start:end])) if dollar_values is not None else 0.0
    weighted_vwap = 0.0
    if vwap_values is not None and volume_values is not None and volume > 0:
        weighted_vwap = float(np.sum(vwap_values[start:end] * volume_values[start:end]) / volume)
    values = {
        "open": float(open_values[start]) if open_values is not None else 0.0,
        "high": float(np.max(high_values[start:end])) if high_values is not None else 0.0,
        "low": float(np.min(low_values[start:end])) if low_values is not None else 0.0,
        "close": float(close_values[end - 1]) if close_values is not None else 0.0,
        "volume": volume,
        "dollar_volume": dollar_volume,
        "trade_count": float(np.sum(trade_count_values[start:end])) if trade_count_values is not None else 0.0,
        "quote_count": float(np.sum(quote_count_values[start:end])) if quote_count_values is not None else 0.0,
        "vwap": float(dollar_volume / volume) if volume > 0 and dollar_volume > 0 else weighted_vwap,
    }
    for index, name in enumerate(BAR_FEATURE_KEYS):
        out[index] = np.float32(values.get(name, 0.0))
    return out


def _today_asof_bar_from_events(rows: np.ndarray) -> np.ndarray:
    out = np.zeros((len(BAR_FEATURE_KEYS),), dtype=np.float32)
    if rows.size == 0:
        return out
    quotes = rows[rows["event_type"] == QUOTE_EVENT_TYPE]
    trades = rows[rows["event_type"] == TRADE_EVENT_TYPE]
    quote_count = float(quotes.size)
    if trades.size:
        prices = _decode_primary_price(trades)
        sizes = np.maximum(0.0, trades["size_primary"].astype(np.float64, copy=False))
        volume = float(np.sum(sizes))
        dollar_volume = float(np.sum(prices * sizes))
        values = {
            "open": float(prices[0]),
            "high": float(np.max(prices)),
            "low": float(np.min(prices)),
            "close": float(prices[-1]),
            "volume": volume,
            "dollar_volume": dollar_volume,
            "trade_count": float(trades.size),
            "quote_count": quote_count,
            "vwap": float(dollar_volume / volume) if volume > 0 else 0.0,
        }
    elif quotes.size:
        last = quotes[-1:]
        ask = _decode_primary_price(last)[0]
        bid = _decode_secondary_price(last)[0]
        mid = float((ask + bid) * 0.5) if ask > 0 and bid > 0 else float(max(ask, bid))
        values = {
            "open": mid,
            "high": mid,
            "low": mid,
            "close": mid,
            "volume": 0.0,
            "dollar_volume": 0.0,
            "trade_count": 0.0,
            "quote_count": quote_count,
            "vwap": mid,
        }
    else:
        return out
    for index, name in enumerate(BAR_FEATURE_KEYS):
        out[index] = np.float32(values.get(name, 0.0))
    return out


def _fill_empty_bar_values(out: dict[str, float], prefix: str) -> None:
    for name in BAR_FEATURE_KEYS:
        out[f"{prefix}_{name}"] = 0.0


def _bar_tensor_row(timeframes: tuple[str, ...], bars: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for row_index, timeframe in enumerate(timeframes):
        for field_index, name in enumerate(BAR_FEATURE_KEYS):
            out[f"{timeframe}_{name}"] = float(bars[row_index, field_index])
    return out


def _future_bar_tensor_row(timeframes: tuple[str, ...], bars: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for row_index, timeframe in enumerate(timeframes):
        for field_index, name in enumerate(FUTURE_BAR_FEATURE_KEYS):
            out[f"{timeframe}_{name}"] = float(bars[row_index, field_index])
    return out


def _select_future_bar_fields(bars: np.ndarray) -> np.ndarray:
    out = np.zeros((bars.shape[0], len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
    for target_index, name in enumerate(FUTURE_BAR_FEATURE_KEYS):
        source_index = BAR_FEATURE_KEYS.index(name)
        out[:, target_index] = bars[:, source_index]
    return out


def _intraday_label_bar_tensor(labels: Mapping[str, np.ndarray], *, horizons: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    if not labels or not horizons:
        return (
            np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32),
            np.zeros((0, 0), dtype=np.bool_),
        )
    first = next(iter(labels.values()))
    batch = int(first.shape[0]) if hasattr(first, "shape") and first.ndim >= 1 else 0
    bars = np.zeros((batch, len(horizons), len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
    mask = np.zeros((batch, len(horizons)), dtype=np.bool_)
    for horizon_index, horizon in enumerate(horizons):
        prefix = f"future_intraday_bar_{horizon}"
        has_trade = labels.get(f"{prefix}_has_trade")
        if has_trade is not None:
            mask[:, horizon_index] = np.asarray(has_trade, dtype=np.bool_)
        for field_index, field_name in enumerate(FUTURE_BAR_FEATURE_KEYS):
            value = labels.get(f"{prefix}_{field_name}")
            if value is not None:
                bars[:, horizon_index, field_index] = np.asarray(value, dtype=np.float32)
    return bars, mask


def _session_features_from_prefix(rows: np.ndarray) -> dict[str, float]:
    if rows.size == 0:
        return _empty_session_features()
    out = _empty_session_features()
    quotes = rows[rows["event_type"] == QUOTE_EVENT_TYPE]
    trades = rows[rows["event_type"] == TRADE_EVENT_TYPE]
    if quotes.size:
        last = quotes[-1:]
        ask = _decode_primary_price(last)[0]
        bid = _decode_secondary_price(last)[0]
        ask_size = float(last["size_primary"][0])
        bid_size = float(last["size_secondary"][0])
        out.update(
            {
                "session_has_quote": 1.0,
                "session_last_ask": float(ask),
                "session_last_bid": float(bid),
                "session_last_ask_size": ask_size,
                "session_last_bid_size": bid_size,
                "session_last_spread": float(max(0.0, ask - bid)),
                "session_last_mid": float((ask + bid) * 0.5) if ask > 0 and bid > 0 else 0.0,
                "session_quote_count_so_far": float(quotes.size),
            }
        )
    if trades.size:
        trade_price = _decode_primary_price(trades)
        trade_size = np.maximum(0.0, trades["size_primary"].astype(np.float64, copy=False))
        out.update(
            {
                "session_has_trade": 1.0,
                "session_last_trade_price": float(trade_price[-1]),
                "session_last_trade_size": float(trade_size[-1]),
                "session_trade_high_so_far": float(np.max(trade_price)),
                "session_trade_low_so_far": float(np.min(trade_price)),
                "session_trade_volume_so_far": float(np.sum(trade_size)),
                "session_trade_count_so_far": float(trades.size),
                "session_trade_vwap_so_far": float(np.sum(trade_price * trade_size) / np.sum(trade_size)) if np.sum(trade_size) > 0 else 0.0,
            }
        )
    return out


def _empty_session_features() -> dict[str, float]:
    return {
        "session_has_quote": 0.0,
        "session_last_ask": 0.0,
        "session_last_bid": 0.0,
        "session_last_ask_size": 0.0,
        "session_last_bid_size": 0.0,
        "session_last_spread": 0.0,
        "session_last_mid": 0.0,
        "session_quote_count_so_far": 0.0,
        "session_has_trade": 0.0,
        "session_last_trade_price": 0.0,
        "session_last_trade_size": 0.0,
        "session_trade_high_so_far": 0.0,
        "session_trade_low_so_far": 0.0,
        "session_trade_volume_so_far": 0.0,
        "session_trade_count_so_far": 0.0,
        "session_trade_vwap_so_far": 0.0,
    }


def _decode_primary_price(rows: np.ndarray) -> np.ndarray:
    scale = rows["event_flags"].astype(np.uint8, copy=False) & 1
    denominator = np.where(scale == 1, 10000.0, 100.0)
    return rows["price_primary_int"].astype(np.float64, copy=False) / denominator


def _decode_secondary_price(rows: np.ndarray) -> np.ndarray:
    scale = (rows["event_flags"].astype(np.uint8, copy=False) >> 1) & 1
    denominator = np.where(scale == 1, 10000.0, 100.0)
    return rows["price_secondary_int"].astype(np.float64, copy=False) / denominator


def _external_timestamp_us(row: Mapping[str, Any], config: ExternalAsOfContextConfig) -> int:
    value = int(row.get(config.timestamp_column, 0))
    unit = str(config.timestamp_unit).lower()
    if unit in {"ns", "nanosecond", "nanoseconds"}:
        return value // 1000
    if unit in {"ms", "millisecond", "milliseconds"}:
        return value * 1000
    if unit in {"s", "sec", "second", "seconds"}:
        return value * 1_000_000
    return value


def _timestamp_us_to_source_unit(timestamp_us: int, config: ExternalAsOfContextConfig) -> int:
    unit = str(config.timestamp_unit).lower()
    if unit in {"ns", "nanosecond", "nanoseconds"}:
        return int(timestamp_us) * 1000
    if unit in {"ms", "millisecond", "milliseconds"}:
        return int(timestamp_us) // 1000
    if unit in {"s", "sec", "second", "seconds"}:
        return int(timestamp_us) // 1_000_000
    return int(timestamp_us)


def _date_time64_from_us(timestamp_us: int, *, scale: int = 9) -> str:
    _ = scale
    return f"fromUnixTimestamp64Micro(toInt64({int(timestamp_us)}), 'UTC')"


def _infer_embedding_dim(embedding_lookup: Mapping[tuple[str, int], np.ndarray]) -> int:
    for value in embedding_lookup.values():
        return int(np.asarray(value).shape[-1])
    return 0


def synthetic_rows_by_ticker(*, tickers: int, rows_per_ticker: int) -> dict[str, np.ndarray]:
    from research.mlops.data.ticker_blocks import make_synthetic_event_rows

    return {
        f"T{index:04d}": make_synthetic_event_rows(int(rows_per_ticker), low_ordinal=0)
        for index in range(int(tickers))
    }


def write_profile_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True) + "\n")
