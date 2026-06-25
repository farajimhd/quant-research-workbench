from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from research.mlops.clickhouse_events import encode_unified_event_window
from research.mlops.rolling_loader.cache import (
    EncodedEventChunk,
    ExternalContextPayload,
    LatestIdRing,
    PerTickerEventCache,
    StableArena,
    padded_id_matrix,
)
from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler


@dataclass(frozen=True, slots=True)
class RollingSamplePointer:
    """A production-compatible sample index.

    The pointer contains stable ids only. Training collators can resolve ids to
    raw chunks/tokens and keep encoders trainable; production collators resolve
    the same ids to cached embeddings.
    """

    ticker: str
    origin_ordinal: int
    origin_timestamp_us: int
    event_chunk_ids: tuple[int, ...]
    global_news_ids: tuple[int, ...]
    ticker_news_ids: tuple[int, ...]
    sec_filing_ids: tuple[int, ...]
    xbrl_ids: tuple[int, ...]
    ticker_macro_bar_ids: tuple[int, ...]
    global_market_bar_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MaterializedRollingBatch:
    """Raw training materialization from sample pointers.

    Shapes:
    - ``headers_uint8``: ``[B, context_chunks, 14]``
    - ``events_uint8``: ``[B, context_chunks, 128, 16]``
    - ``*_ids`` arrays: stable cache ids; zero means padded/missing.
    - ``external_payloads``: optional gathered low-frequency payloads keyed by
      context kind. It is enabled in profiling to expose the true materialize
      cost and can be disabled for ID-only training pipelines.
    """

    tickers: np.ndarray
    origin_ordinal: np.ndarray
    origin_timestamp_us: np.ndarray
    headers_uint8: np.ndarray
    events_uint8: np.ndarray
    global_news_ids: np.ndarray
    ticker_news_ids: np.ndarray
    sec_filing_ids: np.ndarray
    xbrl_ids: np.ndarray
    ticker_macro_bar_ids: np.ndarray
    global_market_bar_ids: np.ndarray
    external_payloads: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        total = int(self.headers_uint8.nbytes + self.events_uint8.nbytes)
        for value in (
            self.origin_ordinal,
            self.origin_timestamp_us,
            self.global_news_ids,
            self.ticker_news_ids,
            self.sec_filing_ids,
            self.xbrl_ids,
            self.ticker_macro_bar_ids,
            self.global_market_bar_ids,
        ):
            total += int(value.nbytes)
        for payload in self.external_payloads.values():
            for array in payload.values():
                total += int(array.nbytes)
        return total


class RollingContextLoader:
    """Stateful cache/index engine shared by training and production.

    This class intentionally has no ClickHouse code. Sources feed chronological
    event rows and low-frequency context updates into it. That makes the cache
    logic easy to profile, test, and reuse from live services.
    """

    def __init__(self, config: RollingLoaderConfig, *, profiler: RollingLoaderProfiler | None = None) -> None:
        self.config = config
        self.profiler = profiler or RollingLoaderProfiler(enabled=False)
        self.event_caches: dict[str, PerTickerEventCache] = {}
        arena_size = max(10_000, int(config.batch_size) * max(8, int(config.context_chunks) + 2))
        self.chunk_arena: StableArena[EncodedEventChunk] = StableArena(max_items=arena_size)
        self.external_arena: StableArena[ExternalContextPayload] = StableArena(max_items=arena_size)
        self.global_news = LatestIdRing(config.global_news_cache_size)
        self.global_market_bars = LatestIdRing(config.global_bar_cache_size)
        self.ticker_news: dict[str, LatestIdRing] = defaultdict(lambda: LatestIdRing(config.ticker_news_cache_size))
        self.sec_filings: dict[str, LatestIdRing] = defaultdict(lambda: LatestIdRing(config.sec_filing_cache_size))
        self.xbrl: dict[str, LatestIdRing] = defaultdict(lambda: LatestIdRing(config.xbrl_cache_size))
        self.ticker_macro_bars: dict[str, LatestIdRing] = defaultdict(lambda: LatestIdRing(config.macro_bar_cache_size))
        self.ready_samples: list[RollingSamplePointer] = []

    def warm_load_events(self, rows_by_ticker: dict[str, np.ndarray]) -> None:
        with self.profiler.stage("warm_load_events", items=sum(int(rows.shape[0]) for rows in rows_by_ticker.values())):
            for ticker, rows in rows_by_ticker.items():
                cache = self._event_cache(str(ticker).upper())
                for row in rows:
                    cache.push_row(row)
                # Warmup is raw high-frequency cache state only. Chunks from
                # the warm range are encoded lazily when a sample references a
                # specific context origin.
                cache.last_origin_encoded = cache.last_ordinal
                self.profiler.incr("warm_tickers", 1)

    def push_event(self, ticker: str, row: np.void) -> RollingSamplePointer | None:
        ticker = str(ticker).upper()
        cache = self._event_cache(ticker)
        with self.profiler.stage("event_cache_push", items=1, bytes_count=int(row.dtype.itemsize)):
            cache.push_row(row)
        sample: RollingSamplePointer | None = None
        with self.profiler.stage("event_chunk_create"):
            created = self._encode_new_chunks(cache, emit_samples=True)
            if created:
                sample = created[-1]
        return sample

    def push_external(
        self,
        *,
        kind: str,
        ticker: str,
        timestamp_us: int,
        payload: ExternalContextPayload,
        global_item: bool = False,
    ) -> int:
        ticker = str(ticker).upper()
        with self.profiler.stage("external_cache_push_pop", items=1, bytes_count=payload.nbytes):
            item_id = self.external_arena.add(ticker=ticker, timestamp_us=int(timestamp_us), payload=payload)
            kind = str(kind)
            if kind == "global_news" or global_item:
                self.global_news.push(item_id)
            elif kind == "ticker_news":
                self.ticker_news[ticker].push(item_id)
            elif kind == "sec_filing":
                self.sec_filings[ticker].push(item_id)
            elif kind == "xbrl":
                self.xbrl[ticker].push(item_id)
            elif kind == "ticker_macro_bar":
                self.ticker_macro_bars[ticker].push(item_id)
            elif kind == "global_market_bar":
                self.global_market_bars.push(item_id)
            else:
                raise ValueError(f"unknown external cache kind: {kind!r}")
            self.profiler.incr(f"external_pushed_{kind}", 1)
        return item_id

    def drain_ready_samples(self, max_count: int | None = None) -> list[RollingSamplePointer]:
        count = len(self.ready_samples) if max_count is None else min(len(self.ready_samples), int(max_count))
        out = self.ready_samples[:count]
        del self.ready_samples[:count]
        return out

    def materialize_training_batch(
        self,
        samples: Iterable[RollingSamplePointer],
        *,
        materialize_external_payloads: bool = False,
    ) -> MaterializedRollingBatch:
        sample_tuple = tuple(samples)
        cfg = self.config
        with self.profiler.stage("batch_materialize", items=len(sample_tuple)):
            batch = len(sample_tuple)
            context = int(cfg.context_chunks)
            headers = np.zeros((batch, context, int(cfg.header_bytes)), dtype=np.uint8)
            events = np.zeros((batch, context, int(cfg.events_per_chunk), int(cfg.event_bytes)), dtype=np.uint8)
            tickers = np.asarray([sample.ticker for sample in sample_tuple], dtype=object)
            origin_ordinal = np.asarray([sample.origin_ordinal for sample in sample_tuple], dtype=np.uint64)
            origin_timestamp_us = np.asarray([sample.origin_timestamp_us for sample in sample_tuple], dtype=np.uint64)
            for sample_index, sample in enumerate(sample_tuple):
                for chunk_index, chunk_id in enumerate(sample.event_chunk_ids):
                    chunk = self.chunk_arena.payload(chunk_id)
                    if chunk is None:
                        continue
                    headers[sample_index, chunk_index] = chunk.header_uint8
                    events[sample_index, chunk_index] = chunk.events_uint8

            global_news_ids = padded_id_matrix((sample.global_news_ids for sample in sample_tuple), width=cfg.global_news_cache_size)
            ticker_news_ids = padded_id_matrix((sample.ticker_news_ids for sample in sample_tuple), width=cfg.ticker_news_cache_size)
            sec_ids = padded_id_matrix((sample.sec_filing_ids for sample in sample_tuple), width=cfg.sec_filing_cache_size)
            xbrl_ids = padded_id_matrix((sample.xbrl_ids for sample in sample_tuple), width=cfg.xbrl_cache_size)
            ticker_bar_ids = padded_id_matrix((sample.ticker_macro_bar_ids for sample in sample_tuple), width=cfg.macro_bar_cache_size)
            global_bar_ids = padded_id_matrix((sample.global_market_bar_ids for sample in sample_tuple), width=cfg.global_bar_cache_size)
            external_payloads = (
                self._materialize_external_payloads(
                    {
                        "global_news": global_news_ids,
                        "ticker_news": ticker_news_ids,
                        "sec_filing": sec_ids,
                        "xbrl": xbrl_ids,
                        "ticker_macro_bar": ticker_bar_ids,
                        "global_market_bar": global_bar_ids,
                    }
                )
                if materialize_external_payloads
                else {}
            )
            out = MaterializedRollingBatch(
                tickers=tickers,
                origin_ordinal=origin_ordinal,
                origin_timestamp_us=origin_timestamp_us,
                headers_uint8=headers,
                events_uint8=events,
                global_news_ids=global_news_ids,
                ticker_news_ids=ticker_news_ids,
                sec_filing_ids=sec_ids,
                xbrl_ids=xbrl_ids,
                ticker_macro_bar_ids=ticker_bar_ids,
                global_market_bar_ids=global_bar_ids,
                external_payloads=external_payloads,
            )
            self.profiler.incr("materialized_samples", batch)
            self.profiler.incr("materialized_batch_bytes", out.nbytes)
            object.__setattr__(out, "profile", {"nbytes": out.nbytes})
            return out

    def cache_summary(self) -> dict[str, int]:
        return {
            "event_tickers": len(self.event_caches),
            "chunk_arena_items": len(self.chunk_arena.items),
            "external_arena_items": len(self.external_arena.items),
            "ready_samples": len(self.ready_samples),
        }

    def _event_cache(self, ticker: str) -> PerTickerEventCache:
        cache = self.event_caches.get(ticker)
        if cache is None:
            cache = PerTickerEventCache(
                ticker=ticker,
                rows_capacity=self.config.event_cache_events_per_ticker,
                chunk_capacity=self.config.chunk_cache_size_per_ticker,
            )
            self.event_caches[ticker] = cache
        return cache

    def _encode_new_chunks(self, cache: PerTickerEventCache, *, emit_samples: bool) -> list[RollingSamplePointer]:
        cfg = self.config
        if not cache.has_minimum_rows(int(cfg.events_per_chunk)) or cache.last_ordinal is None:
            return []
        created_samples: list[RollingSamplePointer] = []
        latest_origin = int(cache.last_ordinal)
        first_ready_origin = cache.ordinal_at_offset(int(cfg.events_per_chunk) - 1)
        if first_ready_origin is None:
            return []
        start_origin = (
            int(first_ready_origin)
            if cache.last_origin_encoded is None
            else int(cache.last_origin_encoded) + int(cfg.chunk_stride_events)
        )
        if start_origin > latest_origin:
            return []
        for origin in range(start_origin, latest_origin + 1, int(cfg.chunk_stride_events)):
            window_with_previous = cache.window_ending(origin, int(cfg.events_per_chunk))
            if window_with_previous is None:
                continue
            window, previous_sip_us = window_with_previous
            encoded = encode_unified_event_window(window, previous_sip_us=previous_sip_us)
            cache.last_origin_encoded = int(origin)
            if isinstance(encoded, str):
                self.profiler.incr(f"chunk_reject_{encoded}", 1)
                continue
            header, events = encoded
            chunk = EncodedEventChunk(
                ticker=cache.ticker,
                origin_ordinal=int(origin),
                origin_timestamp_us=int(window[-1]["sip_timestamp_us"]),
                previous_sip_us=previous_sip_us,
                header_uint8=header.copy(),
                events_uint8=events.copy(),
            )
            chunk_id = self.chunk_arena.add(ticker=cache.ticker, timestamp_us=chunk.origin_timestamp_us, payload=chunk)
            cache.remember_chunk(origin, chunk_id)
            self.profiler.incr("chunks_created", 1)
            if emit_samples and (int(origin) % max(1, int(cfg.sample_stride_events)) == 0):
                sample = self._sample_pointer_for_origin(cache, int(origin), int(chunk.origin_timestamp_us))
                if sample is not None:
                    self.ready_samples.append(sample)
                    created_samples.append(sample)
                    self.profiler.incr("sample_indices_created", 1)
        return created_samples

    def _sample_pointer_for_origin(self, cache: PerTickerEventCache, origin: int, timestamp_us: int) -> RollingSamplePointer | None:
        with self.profiler.stage("sample_index_create", items=1):
            chunk_ids: list[int] = []
            for lag in self.config.context_lags:
                lag_origin = int(origin) - int(lag)
                chunk_id = self._chunk_id_or_encode(cache, lag_origin)
                if chunk_id is None:
                    return None
                chunk_ids.append(int(chunk_id))
            ticker = cache.ticker
            return RollingSamplePointer(
                ticker=ticker,
                origin_ordinal=int(origin),
                origin_timestamp_us=int(timestamp_us),
                event_chunk_ids=tuple(chunk_ids),
                global_news_ids=self.global_news.latest(self.config.global_news_cache_size),
                ticker_news_ids=self.ticker_news[ticker].latest(self.config.ticker_news_cache_size),
                sec_filing_ids=self.sec_filings[ticker].latest(self.config.sec_filing_cache_size),
                xbrl_ids=self.xbrl[ticker].latest(self.config.xbrl_cache_size),
                ticker_macro_bar_ids=self.ticker_macro_bars[ticker].latest(self.config.macro_bar_cache_size),
                global_market_bar_ids=self.global_market_bars.latest(self.config.global_bar_cache_size),
            )

    def _chunk_id_or_encode(self, cache: PerTickerEventCache, origin: int) -> int | None:
        existing = cache.chunk_id(int(origin))
        if existing is not None:
            return int(existing)
        with self.profiler.stage("lazy_context_chunk_encode", items=1):
            window_with_previous = cache.window_ending(int(origin), int(self.config.events_per_chunk))
            if window_with_previous is None:
                return None
            window, previous_sip_us = window_with_previous
            encoded = encode_unified_event_window(window, previous_sip_us=previous_sip_us)
            if isinstance(encoded, str):
                self.profiler.incr(f"chunk_reject_{encoded}", 1)
                return None
            header, events = encoded
            chunk = EncodedEventChunk(
                ticker=cache.ticker,
                origin_ordinal=int(origin),
                origin_timestamp_us=int(window[-1]["sip_timestamp_us"]),
                previous_sip_us=previous_sip_us,
                header_uint8=header.copy(),
                events_uint8=events.copy(),
            )
            chunk_id = self.chunk_arena.add(ticker=cache.ticker, timestamp_us=chunk.origin_timestamp_us, payload=chunk)
            cache.remember_chunk(int(origin), chunk_id)
            self.profiler.incr("chunks_created", 1)
            self.profiler.incr("lazy_chunks_created", 1)
            return int(chunk_id)

    def _materialize_external_payloads(self, id_arrays: dict[str, np.ndarray]) -> dict[str, dict[str, np.ndarray]]:
        with self.profiler.stage("external_payload_materialize", items=sum(int(array.size) for array in id_arrays.values())):
            out: dict[str, dict[str, np.ndarray]] = {}
            for kind, ids in id_arrays.items():
                flat_ids = ids.reshape(-1)
                unique_ids = np.unique(flat_ids[flat_ids != 0])
                payloads = [self.external_arena.payload(int(item_id)) for item_id in unique_ids]
                if not payloads:
                    continue
                arrays: dict[str, list[np.ndarray]] = defaultdict(list)
                for payload in payloads:
                    if payload is None:
                        continue
                    if payload.token_ids is not None:
                        arrays["token_ids"].append(payload.token_ids.reshape(1, *payload.token_ids.shape))
                    if payload.attention_mask is not None:
                        arrays["attention_mask"].append(payload.attention_mask.reshape(1, *payload.attention_mask.shape))
                    if payload.category_ids is not None:
                        arrays["category_ids"].append(payload.category_ids.reshape(1, *payload.category_ids.shape))
                    if payload.numeric_values is not None:
                        arrays["numeric_values"].append(payload.numeric_values.reshape(1, *payload.numeric_values.shape))
                    if payload.time_features is not None:
                        arrays["time_features"].append(payload.time_features.reshape(1, *payload.time_features.shape))
                out[kind] = {name: np.concatenate(parts, axis=0) for name, parts in arrays.items() if parts}
                out[kind]["item_ids"] = unique_ids.astype(np.uint64, copy=False)
                self.profiler.incr(f"external_unique_{kind}", int(unique_ids.shape[0]))
            return out
