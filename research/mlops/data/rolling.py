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
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient, encode_unified_event_window
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES, QUOTE_EVENT_TYPE, TRADE_EVENT_TYPE
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
from research.mlops.data.ticker_blocks import build_future_time_bar_labels


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

    def future(self, *, symbol: str, timestamp_us: int, timeframes: Iterable[str]) -> dict[str, float]:
        symbol = symbol.upper()
        timestamp_ms = int(timestamp_us) // 1000
        out: dict[str, float] = {}
        wanted = set(str(value) for value in timeframes)
        for timeframe in wanted:
            prefix = f"{timeframe}"
            arrays = self._index.get((symbol, timeframe))
            if arrays is None or arrays["bar_start_ms"].size == 0:
                _fill_empty_bar_values(out, prefix)
                continue
            row_index = int(np.searchsorted(arrays["bar_start_ms"], timestamp_ms, side="right"))
            if row_index >= arrays["bar_start_ms"].shape[0]:
                _fill_empty_bar_values(out, prefix)
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
            "news": ExternalAsOfContextConfig(name="news", timestamp_column="timestamp_us", max_items=config.news_max_items),
            "sec_filings": ExternalAsOfContextConfig(name="sec_filings", timestamp_column="timestamp_us", max_items=config.sec_max_items),
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
        session_features = self._materialize_session_features(sample_tuple)
        labels = self._materialize_future_labels(sample_tuple)
        labels.update(self._materialize_intraday_future_labels(sample_tuple))
        macro_features.update(session_features)
        external = {
            name: [store.asof(ticker=sample.ticker, timestamp_us=sample.origin_timestamp_us) for sample in sample_tuple]
            for name, store in self.external_contexts.items()
        }
        text_inputs = self._materialize_text_inputs(external)
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
            context_mask=mask,
            ticker=tickers,
            origin_ordinal=origin_ord,
            origin_timestamp_us=origin_ts,
            chunk_origin_ordinal=chunk_origin_ordinal,
            chunk_origin_timestamp_us=chunk_origin_ts,
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
        macro_features, global_features = self._materialize_bar_features(sample_tuple)
        external = {
            name: [store.asof(ticker=sample.ticker, timestamp_us=sample.origin_timestamp_us) for sample in sample_tuple]
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

    def _materialize_future_labels(self, samples: tuple[RollingSampleIndex, ...]) -> dict[str, np.ndarray]:
        rows = [
            self.macro_bars.future(symbol=sample.ticker, timestamp_us=sample.origin_timestamp_us, timeframes=self.config.label_timeframes)
            for sample in samples
        ]
        return {f"future_{key}": value for key, value in _rows_to_feature_arrays(rows).items()}

    def _materialize_intraday_future_labels(self, samples: tuple[RollingSampleIndex, ...]) -> dict[str, np.ndarray]:
        """Future same-queue trade bars for short horizons.

        These labels are derived from the current in-memory event queue, so they
        line up with production stream semantics. They intentionally complement
        the macro future labels above, which come from offline daily/weekly/monthly
        bars.
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
            labels[f"{prefix}_trade_count"] = np.zeros((batch,), dtype=np.uint32)

        by_ticker: dict[str, list[tuple[int, int]]] = {}
        for batch_index, sample in enumerate(samples):
            ticker_rows = self.rows_by_ticker.get(sample.ticker)
            if ticker_rows is None or ticker_rows.size == 0:
                continue
            origin_offset = int(sample.origin_ordinal) - int(ticker_rows["ordinal"][0])
            if 0 <= origin_offset < ticker_rows.shape[0]:
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
            low = int(ticker_rows["ordinal"][0])
            origin_offset = int(sample.origin_ordinal) - low
            if origin_offset < 0:
                rows.append(_empty_session_features())
                continue
            rows.append(_session_features_from_prefix(ticker_rows[: origin_offset + 1]))
        return _rows_to_feature_arrays(rows)

    def _materialize_text_inputs(self, external: Mapping[str, list[list[dict[str, Any]]]]) -> dict[str, dict[str, np.ndarray]]:
        out: dict[str, dict[str, np.ndarray]] = {}
        for name, max_items in (("news", self.config.news_max_items), ("sec_filings", self.config.sec_max_items)):
            rows_by_sample = external.get(name)
            if rows_by_sample is None:
                continue
            batch = len(rows_by_sample)
            max_items = int(max_items)
            texts: list[str] = []
            source_ts = np.zeros((batch, max_items), dtype=np.int64)
            item_mask = np.zeros((batch, max_items), dtype=np.bool_)
            for sample_index, rows in enumerate(rows_by_sample):
                selected = list(rows)[-max_items:]
                for item_index, row in enumerate(selected):
                    texts.append(_row_to_model_text(name, row))
                    source_ts[sample_index, item_index] = int(row.get("timestamp_us", 0) or 0)
                    item_mask[sample_index, item_index] = True
                for _ in range(max_items - len(selected)):
                    texts.append("")
            encoded = self._text_tokenizer.encode(texts)
            out[name] = {
                "input_ids": encoded["input_ids"].reshape(batch, max_items, -1),
                "attention_mask": encoded["attention_mask"].reshape(batch, max_items, -1),
                "item_mask": item_mask,
                "timestamp_us": source_ts,
            }
        return out

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
        timestamp_us = np.zeros((batch, max_items), dtype=np.int64)
        value = np.zeros((batch, max_items), dtype=np.float32)
        fiscal_year = np.zeros((batch, max_items), dtype=np.int16)
        age_days = np.zeros((batch, max_items), dtype=np.float32)
        period_end_days = np.zeros((batch, max_items), dtype=np.int32)
        taxonomy_id = np.zeros((batch, max_items), dtype=np.uint32)
        tag_id = np.zeros((batch, max_items), dtype=np.uint32)
        unit_id = np.zeros((batch, max_items), dtype=np.uint32)
        form_id = np.zeros((batch, max_items), dtype=np.uint32)
        row_kind_id = np.zeros((batch, max_items), dtype=np.uint8)
        calendar_period_id = np.zeros((batch, max_items), dtype=np.uint32)
        location_id = np.zeros((batch, max_items), dtype=np.uint32)
        mapping_confidence = np.zeros((batch, max_items), dtype=np.float32)
        for sample_index, rows in enumerate(rows_by_sample):
            selected = list(rows)[-max_items:]
            origin_us = int(samples[sample_index].origin_timestamp_us) if sample_index < len(samples) else 0
            for item_index, row in enumerate(selected):
                row_ts = int(row.get("timestamp_us", 0) or 0)
                mask[sample_index, item_index] = True
                timestamp_us[sample_index, item_index] = row_ts
                value[sample_index, item_index] = _safe_float32(row.get("value", 0.0))
                fiscal_year[sample_index, item_index] = int(row.get("fiscal_year", 0) or 0)
                age_days[sample_index, item_index] = max(0.0, float(origin_us - row_ts) / 86_400_000_000.0) if origin_us and row_ts else 0.0
                period_end_days[sample_index, item_index] = _date_to_epoch_day(row.get("period_end_date"))
                taxonomy_id[sample_index, item_index] = _stable_uint32(row.get("taxonomy", ""))
                tag_id[sample_index, item_index] = _stable_uint32(row.get("tag", ""))
                unit_id[sample_index, item_index] = _stable_uint32(row.get("unit_code", ""))
                form_id[sample_index, item_index] = _stable_uint32(row.get("form_type", ""))
                row_kind_id[sample_index, item_index] = 2 if str(row.get("xbrl_row_kind", "")) == "frame_observation" else 1
                calendar_period_id[sample_index, item_index] = _stable_uint32(row.get("calendar_period_code", ""))
                location_id[sample_index, item_index] = _stable_uint32(row.get("location_code", ""))
                mapping_confidence[sample_index, item_index] = _safe_float32(row.get("mapping_confidence_score", 0.0))
        return {
            "mask": mask,
            "timestamp_us": timestamp_us,
            "value": value,
            "fiscal_year": fiscal_year,
            "age_days": age_days,
            "period_end_days": period_end_days,
            "taxonomy_id": taxonomy_id,
            "tag_id": tag_id,
            "unit_id": unit_id,
            "form_id": form_id,
            "row_kind_id": row_kind_id,
            "calendar_period_id": calendar_period_id,
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
        if "news" in enabled:
            out["news"] = self._fetch_q_live_news(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        if "sec_filings" in enabled:
            out["sec_filings"] = self._fetch_q_live_sec_filings(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        if "xbrl" in enabled:
            out["xbrl"] = self._fetch_q_live_xbrl(
                start_timestamp_us=start_timestamp_us,
                end_timestamp_us=end_timestamp_us,
                tickers=ticker_tuple,
            )
        out.update(self.fetch_external_contexts(start_timestamp_us=start_timestamp_us, end_timestamp_us=end_timestamp_us, tickers=ticker_tuple))
        return out

    def _fetch_q_live_news(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.q_live_database)
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_expr = _date_time64_from_us(max(0, int(start_timestamp_us) - int(self.config.news_lookback_days) * 86_400_000_000))
        end_expr = _date_time64_from_us(int(end_timestamp_us))
        query = f"""
SELECT
    nt.ticker AS ticker,
    toUnixTimestamp64Micro(nt.published_at_utc) AS timestamp_us,
    nt.canonical_news_id AS source_id,
    nt.provider AS provider,
    nt.provider_article_id AS provider_article_id,
    n.title AS title,
    n.teaser AS teaser,
    substring(n.normalized_full_text, 1, 12000) AS text,
    n.article_url AS article_url,
    n.url_domain AS url_domain,
    n.channels AS channels,
    n.provider_tags AS provider_tags,
    n.content_quality_flags AS quality_flags
FROM {database}.benzinga_news_ticker_v1 AS nt
ANY INNER JOIN {database}.benzinga_news_normalized_v1 AS n
    ON nt.canonical_news_id = n.canonical_news_id
WHERE nt.ticker IN ({ticker_sql})
  AND nt.published_at_utc >= {start_expr}
  AND nt.published_at_utc <= {end_expr}
ORDER BY nt.ticker, nt.published_at_utc DESC, nt.canonical_news_id
LIMIT {int(self.config.news_max_items)} BY nt.ticker
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_q_live_sec_filings(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.q_live_database)
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_expr = _date_time64_from_us(max(0, int(start_timestamp_us) - int(self.config.sec_lookback_days) * 86_400_000_000))
        end_expr = _date_time64_from_us(int(end_timestamp_us))
        query = f"""
WITH has_event_bridge AS
(
    SELECT count() > 0 AS has_rows
    FROM {database}.feature_sec_event_market_bridge_v1
)
SELECT *
FROM
(
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(b.accepted_at_utc) AS timestamp_us,
        f.accession_number AS source_id,
        f.accession_number AS accession_number,
        f.cik AS cik,
        f.form_type AS form_type,
        b.mapping_confidence_score AS mapping_confidence_score,
        b.bridge_id AS bridge_id,
        b.security_id AS security_id,
        b.listing_id AS listing_id,
        b.symbol_id AS symbol_id,
        f.filing_id AS filing_id,
        ifNull(f.company_name, '') AS company_name,
        ifNull(f.primary_document, '') AS primary_document,
        ifNull(f.primary_document_url, '') AS primary_document_url,
        ifNull(f.filing_detail_url, '') AS filing_detail_url,
        ifNull(f.items, '') AS items
    FROM {database}.sec_filing_v2 AS f
    INNER JOIN {database}.feature_sec_event_market_bridge_v1 AS b
        ON b.cik = f.cik
       AND b.accession_number = f.accession_number
    WHERE (SELECT has_rows FROM has_event_bridge)
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND b.accepted_at_utc >= {start_expr}
      AND b.accepted_at_utc <= {end_expr}
    UNION ALL
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(f.accepted_at_utc) AS timestamp_us,
        f.accession_number AS source_id,
        f.accession_number AS accession_number,
        f.cik AS cik,
        f.form_type AS form_type,
        b.confidence_score AS mapping_confidence_score,
        b.bridge_id AS bridge_id,
        b.security_id AS security_id,
        b.listing_id AS listing_id,
        b.symbol_id AS symbol_id,
        f.filing_id AS filing_id,
        ifNull(f.company_name, '') AS company_name,
        ifNull(f.primary_document, '') AS primary_document,
        ifNull(f.primary_document_url, '') AS primary_document_url,
        ifNull(f.filing_detail_url, '') AS filing_detail_url,
        ifNull(f.items, '') AS items
    FROM {database}.sec_filing_v2 AS f
    INNER JOIN {database}.id_sec_market_bridge_v1 AS b
        ON b.cik = f.cik
    WHERE NOT (SELECT has_rows FROM has_event_bridge)
      AND f.accepted_at_utc IS NOT NULL
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND (b.accession_number IS NULL OR b.accession_number = '' OR b.accession_number = f.accession_number)
      AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
      AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
      AND b.mapping_status IN ('active', 'mapped', 'accepted', '')
      AND f.accepted_at_utc >= {start_expr}
      AND f.accepted_at_utc <= {end_expr}
)
ORDER BY ticker, timestamp_us DESC, accession_number
LIMIT {int(self.config.sec_max_items)} BY ticker
FORMAT JSONEachRow
"""
        filings = [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]
        self._attach_q_live_sec_text(database=database, filings=filings)
        filings.sort(key=lambda row: (str(row.get("ticker", "")), int(row.get("timestamp_us", 0)), str(row.get("accession_number", ""))))
        return filings

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
FROM {database}.sec_filing_text_v2
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

    def _fetch_q_live_xbrl(self, *, start_timestamp_us: int, end_timestamp_us: int, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        database = quote_ident(self.config.q_live_database)
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        start_expr = _date_time64_from_us(max(0, int(start_timestamp_us) - int(self.config.xbrl_lookback_days) * 86_400_000_000), scale=3)
        end_expr = _date_time64_from_us(int(end_timestamp_us), scale=3)
        query = f"""
WITH has_event_bridge AS
(
    SELECT count() > 0 AS has_rows
    FROM {database}.feature_sec_event_market_bridge_v1
)
SELECT *
FROM
(
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(b.accepted_at_utc) AS timestamp_us,
        x.company_fact_id AS source_id,
        x.cik AS cik,
        x.issuer_id AS issuer_id,
        x.taxonomy AS taxonomy,
        x.tag AS tag,
        x.unit_code AS unit_code,
        ifNull(x.fiscal_year, 0) AS fiscal_year,
        ifNull(x.fiscal_period, '') AS fiscal_period,
        ifNull(x.form_type, '') AS form_type,
        ifNull(x.accession_number, '') AS accession_number,
        x.period_end_date AS period_end_date,
        x.value AS value,
        '' AS calendar_period_code,
        '' AS location_code,
        'company_fact' AS xbrl_row_kind,
        b.bridge_id AS bridge_id,
        b.mapping_confidence_score AS mapping_confidence_score
    FROM {database}.sec_xbrl_company_fact_v1 AS x
    INNER JOIN {database}.feature_sec_event_market_bridge_v1 AS b
        ON b.cik = x.cik
       AND b.accession_number = x.accession_number
    WHERE (SELECT has_rows FROM has_event_bridge)
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND x.accession_number IS NOT NULL
      AND x.accession_number != ''
      AND b.accepted_at_utc >= {start_expr}
      AND b.accepted_at_utc <= {end_expr}
    UNION ALL
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(f.accepted_at_utc) AS timestamp_us,
        x.company_fact_id AS source_id,
        x.cik AS cik,
        x.issuer_id AS issuer_id,
        x.taxonomy AS taxonomy,
        x.tag AS tag,
        x.unit_code AS unit_code,
        ifNull(x.fiscal_year, 0) AS fiscal_year,
        ifNull(x.fiscal_period, '') AS fiscal_period,
        ifNull(x.form_type, '') AS form_type,
        ifNull(x.accession_number, '') AS accession_number,
        x.period_end_date AS period_end_date,
        x.value AS value,
        '' AS calendar_period_code,
        '' AS location_code,
        'company_fact' AS xbrl_row_kind,
        b.bridge_id AS bridge_id,
        b.confidence_score AS mapping_confidence_score
    FROM {database}.sec_xbrl_company_fact_v1 AS x
    INNER JOIN {database}.sec_filing_v2 AS f
        ON f.cik = x.cik
       AND f.accession_number = x.accession_number
    INNER JOIN {database}.id_sec_market_bridge_v1 AS b
        ON b.cik = x.cik
    WHERE NOT (SELECT has_rows FROM has_event_bridge)
      AND x.accession_number IS NOT NULL
      AND x.accession_number != ''
      AND f.accepted_at_utc IS NOT NULL
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND (b.accession_number IS NULL OR b.accession_number = '' OR b.accession_number = x.accession_number)
      AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
      AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
      AND b.mapping_status IN ('active', 'mapped', 'accepted', '')
      AND f.accepted_at_utc >= {start_expr}
      AND f.accepted_at_utc <= {end_expr}
    UNION ALL
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(b.accepted_at_utc) AS timestamp_us,
        o.frame_observation_id AS source_id,
        o.cik AS cik,
        o.issuer_id AS issuer_id,
        o.taxonomy AS taxonomy,
        o.tag AS tag,
        o.unit_code AS unit_code,
        toUInt32(0) AS fiscal_year,
        '' AS fiscal_period,
        '' AS form_type,
        o.accession_number AS accession_number,
        o.period_end_date AS period_end_date,
        o.value AS value,
        o.calendar_period_code AS calendar_period_code,
        ifNull(o.location_code, '') AS location_code,
        'frame_observation' AS xbrl_row_kind,
        b.bridge_id AS bridge_id,
        b.mapping_confidence_score AS mapping_confidence_score
    FROM {database}.sec_xbrl_frame_observation_v1 AS o
    INNER JOIN {database}.feature_sec_event_market_bridge_v1 AS b
        ON b.cik = o.cik
       AND b.accession_number = o.accession_number
    WHERE (SELECT has_rows FROM has_event_bridge)
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND b.accepted_at_utc >= {start_expr}
      AND b.accepted_at_utc <= {end_expr}
    UNION ALL
    SELECT
        ifNull(b.ticker, '') AS ticker,
        toUnixTimestamp64Micro(f.accepted_at_utc) AS timestamp_us,
        o.frame_observation_id AS source_id,
        o.cik AS cik,
        o.issuer_id AS issuer_id,
        o.taxonomy AS taxonomy,
        o.tag AS tag,
        o.unit_code AS unit_code,
        toUInt32(0) AS fiscal_year,
        '' AS fiscal_period,
        '' AS form_type,
        o.accession_number AS accession_number,
        o.period_end_date AS period_end_date,
        o.value AS value,
        o.calendar_period_code AS calendar_period_code,
        ifNull(o.location_code, '') AS location_code,
        'frame_observation' AS xbrl_row_kind,
        b.bridge_id AS bridge_id,
        b.confidence_score AS mapping_confidence_score
    FROM {database}.sec_xbrl_frame_observation_v1 AS o
    INNER JOIN {database}.sec_filing_v2 AS f
        ON f.cik = o.cik
       AND f.accession_number = o.accession_number
    INNER JOIN {database}.id_sec_market_bridge_v1 AS b
        ON b.cik = o.cik
    WHERE NOT (SELECT has_rows FROM has_event_bridge)
      AND f.accepted_at_utc IS NOT NULL
      AND ifNull(b.ticker, '') IN ({ticker_sql})
      AND (b.accession_number IS NULL OR b.accession_number = '' OR b.accession_number = o.accession_number)
      AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
      AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
      AND b.mapping_status IN ('active', 'mapped', 'accepted', '')
      AND f.accepted_at_utc >= {start_expr}
      AND f.accepted_at_utc <= {end_expr}
)
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


def _fallback_tokenize(texts: list[str], *, max_tokens: int) -> dict[str, np.ndarray]:
    input_ids = np.zeros((len(texts), int(max_tokens)), dtype=np.int32)
    attention_mask = np.zeros((len(texts), int(max_tokens)), dtype=np.uint8)
    for row_index, text in enumerate(texts):
        tokens = re.findall(r"\w+|[^\w\s]", str(text).lower(), flags=re.UNICODE)[: int(max_tokens)]
        for token_index, token in enumerate(tokens):
            input_ids[row_index, token_index] = int(_stable_uint32(token) % 151_936) + 1
            attention_mask[row_index, token_index] = 1
    return {"input_ids": input_ids, "attention_mask": attention_mask}


def _row_to_model_text(name: str, row: Mapping[str, Any]) -> str:
    if name == "news":
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
        texts = row.get("texts") or []
        body_parts: list[str] = []
        if isinstance(texts, list):
            for text_row in texts:
                if isinstance(text_row, Mapping):
                    kind = str(text_row.get("text_kind", "") or "")
                    body = str(text_row.get("text", "") or "")
                    body_parts.append(f"{kind}\n{body}" if kind else body)
        return "\n".join(part for part in [heading, *body_parts] if part).strip()
    payload = row.get("text") or row.get("headline") or row.get("title") or ""
    return str(payload)


def _stable_uint32(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    digest = hashlib.blake2b(text.encode("utf-8", errors="ignore"), digest_size=4).digest()
    return int.from_bytes(digest, "little", signed=False)


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


def _fill_empty_bar_values(out: dict[str, float], prefix: str) -> None:
    for name in ("open", "high", "low", "close", "volume", "dollar_volume", "trade_count", "quote_count", "vwap"):
        out[f"{prefix}_{name}"] = 0.0


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
