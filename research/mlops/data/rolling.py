from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient, encode_unified_event_window
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES
from research.mlops.data.config import ExternalAsOfContextConfig, RollingMarketDataConfig
from research.mlops.data.contracts import (
    ChunkWindowIndex,
    CompactEvent,
    RollingProductionBatch,
    RollingSampleIndex,
    RollingTrainingBatch,
)
from research.mlops.data.market_events import events_to_rows
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler


@dataclass(frozen=True, slots=True)
class HistoricalDayFetchResult:
    rows_by_ticker: dict[str, np.ndarray]
    event_date: str
    rows_returned: int
    fetch_seconds: float


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
        value_names = ("open", "high", "low", "close", "volume", "dollar_volume", "trade_count", "quote_count", "vwap")
        for key, rows in grouped.items():
            rows.sort(key=lambda item: int(item.get("bar_start_ms", 0)))
            index[key] = {"bar_start_ms": np.asarray([int(row.get("bar_start_ms", 0)) for row in rows], dtype=np.int64)}
            for name in value_names:
                index[key][name] = np.asarray([float(row.get(name, 0.0) or 0.0) for row in rows], dtype=np.float32)
        object.__setattr__(self, "_index", index)

    def asof(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> dict[str, float]:
        symbol = symbol.upper()
        timestamp_ms = int(timestamp_us) // 1000
        out: dict[str, float] = {}
        wanted = set(str(value) for value in timeframes)
        for timeframe in wanted:
            prefix = f"{timeframe}"
            arrays = self._index.get((symbol, timeframe))
            if arrays is None or arrays["bar_start_ms"].size == 0:
                for name in ("open", "high", "low", "close", "volume", "dollar_volume", "trade_count", "quote_count", "vwap"):
                    out[f"{prefix}_{name}"] = 0.0
                continue
            row_index = int(np.searchsorted(arrays["bar_start_ms"], timestamp_ms, side="right") - 1)
            if row_index < 0:
                for name in ("open", "high", "low", "close", "volume", "dollar_volume", "trade_count", "quote_count", "vwap"):
                    out[f"{prefix}_{name}"] = 0.0
                continue
            for name in ("open", "high", "low", "close", "volume", "dollar_volume", "trade_count", "quote_count", "vwap"):
                out[f"{prefix}_{name}"] = float(arrays[name][row_index])
        return out


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
            rows_for_ticker.sort(key=lambda item: int(item.get(self.config.timestamp_column, 0)))

    def asof(self, *, ticker: str, timestamp_us: int) -> list[dict[str, Any]]:
        rows = self.rows_by_ticker.get(ticker.upper(), [])
        if not rows:
            return []
        lower_bound = None
        if int(self.config.max_age_microseconds) > 0:
            lower_bound = int(timestamp_us) - int(self.config.max_age_microseconds)
        selected: list[dict[str, Any]] = []
        for row in reversed(rows):
            row_ts = int(row.get(self.config.timestamp_column, 0))
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
    """Production embedding lookup keyed by `(ticker, chunk_origin_ordinal)`."""

    embedding_dim: int
    _values: dict[tuple[str, int], np.ndarray] = field(default_factory=dict)

    def add(self, *, ticker: str, origin_ordinal: int, embedding: np.ndarray) -> None:
        value = np.asarray(embedding, dtype=np.float32)
        if value.shape[-1] != int(self.embedding_dim):
            raise ValueError(f"Expected embedding_dim={self.embedding_dim}, got shape={value.shape}")
        self._values[(ticker.upper(), int(origin_ordinal))] = value

    def as_mapping(self) -> Mapping[tuple[str, int], np.ndarray]:
        return self._values


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
        self.external_contexts: dict[str, ExternalAsOfStore] = {
            source.name: ExternalAsOfStore(source) for source in config.external_contexts
        }

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

    def build_ready_indices(self, *, max_samples: int = 0) -> tuple[RollingSampleIndex, ...]:
        samples: list[RollingSampleIndex] = []
        context = int(self.config.events_per_chunk)
        lags = self.context_lags
        if not lags:
            return ()
        min_origin_offset = int(self.config.max_context_lag) + context - 1
        stride = max(1, int(self.config.sample_stride_events))
        cap = int(max_samples or self.config.max_ready_samples)

        for ticker in sorted(self.rows_by_ticker):
            rows = self.rows_by_ticker[ticker]
            if rows.shape[0] <= min_origin_offset:
                continue
            start_offset = max(min_origin_offset, self._processed_offsets.get(ticker, min_origin_offset))
            origin_offsets = np.arange(start_offset, rows.shape[0], stride, dtype=np.int64)
            if origin_offsets.size == 0:
                continue
            if rows.shape[0] > 1 and np.any(rows["ordinal"][1:] != rows["ordinal"][:-1] + 1):
                origin_offsets = _filter_contiguous_origins(rows, origin_offsets, lags, context)
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
                            start_ordinal=int(rows["ordinal"][start]),
                            end_ordinal=int(rows["ordinal"][end]),
                            origin_ordinal=int(rows["ordinal"][chunk_origin_offset]),
                            origin_timestamp_us=int(rows["sip_timestamp_us"][chunk_origin_offset]),
                        )
                    )
                samples.append(
                    RollingSampleIndex(
                        ticker=ticker,
                        origin_ordinal=int(rows["ordinal"][origin_offset]),
                        origin_timestamp_us=int(rows["sip_timestamp_us"][origin_offset]),
                        chunk_windows=tuple(chunk_windows),
                        macro_asof_timestamp_us=int(rows["sip_timestamp_us"][origin_offset]),
                        global_asof_timestamp_us=int(rows["sip_timestamp_us"][origin_offset]),
                    )
                )
                if cap > 0 and len(samples) >= cap:
                    return tuple(samples)
        return tuple(samples)

    def materialize_training_batch(self, samples: Iterable[RollingSampleIndex], *, batch_id: int = 0) -> RollingTrainingBatch:
        sample_tuple = tuple(samples)
        profiler = DataPrepProfiler("rolling_training_materialize", batch_id=int(batch_id), enabled=True)
        batch = len(sample_tuple)
        context_chunks = len(self.context_lags)
        headers = np.zeros((batch, context_chunks, HEADER_BYTES), dtype=np.uint8)
        events = np.zeros((batch, context_chunks, int(self.config.events_per_chunk), EVENT_BYTES), dtype=np.uint8)
        mask = np.zeros((batch, context_chunks), dtype=np.bool_)
        chunk_origin_ordinal = np.zeros((batch, context_chunks), dtype=np.int64)
        chunk_origin_ts = np.zeros((batch, context_chunks), dtype=np.int64)
        tickers = np.asarray([sample.ticker for sample in sample_tuple], dtype=object)
        origin_ord = np.asarray([sample.origin_ordinal for sample in sample_tuple], dtype=np.int64)
        origin_ts = np.asarray([sample.origin_timestamp_us for sample in sample_tuple], dtype=np.int64)

        with profiler.stage("encode_compact_windows", count=batch * context_chunks):
            for sample_index, sample in enumerate(sample_tuple):
                rows = self.rows_by_ticker.get(sample.ticker)
                if rows is None:
                    continue
                low_ordinal = int(rows["ordinal"][0])
                for context_index, window in enumerate(sample.chunk_windows):
                    start = int(window.start_ordinal) - low_ordinal
                    end = int(window.end_ordinal) - low_ordinal + 1
                    if start < 0 or end > rows.shape[0] or end - start != int(self.config.events_per_chunk):
                        continue
                    previous_sip_us = int(rows["sip_timestamp_us"][start - 1]) if start > 0 else None
                    encoded = encode_unified_event_window(rows[start:end], previous_sip_us=previous_sip_us)
                    if isinstance(encoded, str):
                        continue
                    headers[sample_index, context_index], events[sample_index, context_index] = encoded
                    mask[sample_index, context_index] = True
                    chunk_origin_ordinal[sample_index, context_index] = int(window.origin_ordinal)
                    chunk_origin_ts[sample_index, context_index] = int(window.origin_timestamp_us)

        macro_features, global_features = self._materialize_bar_features(sample_tuple)
        external = {
            name: [store.asof(ticker=sample.ticker, timestamp_us=sample.origin_timestamp_us) for sample in sample_tuple]
            for name, store in self.external_contexts.items()
        }
        profile = profiler.finish()
        profile.rows_read = int(sum(len(sample.chunk_windows) * self.config.events_per_chunk for sample in sample_tuple))
        profile.chunks_created = int(mask.sum())
        profile.samples_created = int(batch)
        profile.output_batches_created = 1
        return RollingTrainingBatch(
            headers_uint8=headers,
            events_uint8=events,
            context_mask=mask,
            ticker=tickers,
            origin_ordinal=origin_ord,
            origin_timestamp_us=origin_ts,
            chunk_origin_ordinal=chunk_origin_ordinal,
            chunk_origin_timestamp_us=chunk_origin_ts,
            macro_features=macro_features,
            global_features=global_features,
            external_context=external,
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
        macro_features, global_features = self._materialize_bar_features(sample_tuple)
        external = {
            name: [store.asof(ticker=sample.ticker, timestamp_us=sample.origin_timestamp_us) for sample in sample_tuple]
            for name, store in self.external_contexts.items()
        }
        profile = profiler.finish()
        profile.samples_created = len(sample_tuple)
        profile.embeddings_created = int(mask.sum())
        profile.output_batches_created = 1
        return RollingProductionBatch(
            market_embeddings=market,
            market_mask=mask,
            samples=sample_tuple,
            macro_features=macro_features,
            global_features=global_features,
            external_context=external,
            profile=profile,
        )

    def mark_processed(self, samples: Iterable[RollingSampleIndex]) -> None:
        for sample in samples:
            rows = self.rows_by_ticker.get(sample.ticker)
            if rows is None or rows.size == 0:
                continue
            offset = int(sample.origin_ordinal) - int(rows["ordinal"][0]) + 1
            self._processed_offsets[sample.ticker] = max(self._processed_offsets.get(sample.ticker, 0), offset)

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
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        macro_rows: list[dict[str, float]] = []
        global_rows: dict[str, list[dict[str, float]]] = {symbol: [] for symbol in self.config.global_symbols}
        for sample in samples:
            macro_rows.append(
                self.macro_bars.asof(symbol=sample.ticker, timestamp_us=sample.origin_timestamp_us, timeframes=self.config.macro_timeframes)
            )
            for symbol in self.config.global_symbols:
                global_rows[symbol].append(
                    self.macro_bars.asof(symbol=symbol, timestamp_us=sample.global_asof_timestamp_us, timeframes=self.config.macro_timeframes)
                )
        macro = _rows_to_feature_arrays(macro_rows)
        global_features = {f"{symbol}_{key}": value for symbol, rows in global_rows.items() for key, value in _rows_to_feature_arrays(rows).items()}
        return macro, global_features


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
  AND timeframe IN ({timeframe_sql})
  AND bar_start >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC') - INTERVAL 40 DAY
  AND bar_start < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC') + INTERVAL 40 DAY
ORDER BY sym, timeframe, bar_start
FORMAT JSONEachRow
"""
        started = time.perf_counter()
        text = self.text_client.execute(query)
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return MacroBarFrame(rows=rows, fetch_seconds=time.perf_counter() - started)


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


def _filter_contiguous_origins(rows: np.ndarray, origin_offsets: np.ndarray, lags: tuple[int, ...], context: int) -> np.ndarray:
    ordinals = rows["ordinal"].astype(np.int64, copy=False)
    valid = np.ones((origin_offsets.shape[0],), dtype=np.bool_)
    for index, origin_offset in enumerate(origin_offsets.tolist()):
        for lag in lags:
            end = int(origin_offset) - int(lag)
            start = end - int(context) + 1
            if start < 0 or ordinals[end] - ordinals[start] != int(context) - 1:
                valid[index] = False
                break
    return origin_offsets[valid]


def _rows_to_feature_arrays(rows: list[dict[str, float]]) -> dict[str, np.ndarray]:
    if not rows:
        return {}
    keys = sorted({key for row in rows for key in row})
    return {key: np.asarray([float(row.get(key, 0.0) or 0.0) for row in rows], dtype=np.float32) for key in keys}


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
