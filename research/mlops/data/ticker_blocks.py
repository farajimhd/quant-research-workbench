from __future__ import annotations

import datetime as dt
import json
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, MutableMapping

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string
from research.mlops.clickhouse_events import (
    EVENT_ROW_DTYPE,
    EventSpan,
    PersistentClickHouseBytesClient,
    encode_unified_event_window,
    fetch_spans,
)
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES, QUOTE_EVENT_TYPE, TRADE_EVENT_TYPE
from research.mlops.data.config import TickerBlockDataConfig, TimeBarHorizon
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler


@dataclass(frozen=True, slots=True)
class TickerCursor:
    ticker: str
    first_ordinal: int
    next_origin_ordinal: int
    last_ordinal: int
    event_count: int
    bucket: int = 0

    @property
    def active(self) -> bool:
        return int(self.next_origin_ordinal) <= int(self.last_ordinal)


@dataclass(frozen=True, slots=True)
class TickerBlockRequest:
    ticker: str
    low_ordinal: int
    high_ordinal: int
    origin_start_ordinal: int
    origin_end_ordinal: int
    expected_rows: int


@dataclass(frozen=True, slots=True)
class EventTimeBarBatch:
    header_uint8: np.ndarray
    events_uint8: np.ndarray
    ticker: np.ndarray
    origin_ordinal: np.ndarray
    origin_timestamp_us: np.ndarray
    labels: dict[str, np.ndarray]
    profile: DataPrepProfile
    reject_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FetchResult:
    rows_by_ticker: dict[str, np.ndarray]
    requests: list[TickerBlockRequest]
    fetch_seconds: float
    rows_returned: int
    query_mode: str


@dataclass(frozen=True, slots=True)
class FutureTimeBarLabelState:
    trade_ts: np.ndarray
    trade_price: np.ndarray
    trade_size: np.ndarray
    volume_prefix: np.ndarray
    price_rmq: "_RangeMinMax | None"


@dataclass(slots=True)
class ClickHouseTickerBlockBatchProvider:
    """Read chronological ticker blocks and create chunk+bar-label batches.

    Ticker selection is without replacement inside each ticker epoch. Ordinal
    cursors are advanced only after a batch is built successfully, so a persisted
    scheduler state can resume safely after interruption.
    """

    config: TickerBlockDataConfig
    text_client: ClickHouseHttpClient
    bytes_client: PersistentClickHouseBytesClient
    scheduler: TickerEpochScheduler
    batch_id: int = 0

    @classmethod
    def from_clickhouse(
        cls,
        *,
        config: TickerBlockDataConfig,
        text_client: ClickHouseHttpClient,
        bytes_client: PersistentClickHouseBytesClient,
        max_tickers: int = 0,
    ) -> "ClickHouseTickerBlockBatchProvider":
        if config.state_path is not None and config.state_path.exists():
            scheduler = TickerEpochScheduler.load(config.state_path)
        else:
            cursors = load_ticker_cursors_from_index(text_client, config, limit=max_tickers)
            scheduler = TickerEpochScheduler.from_cursors(cursors, seed=config.seed)
            if config.state_path is not None:
                scheduler.save(config.state_path)
        return cls(config=config, text_client=text_client, bytes_client=bytes_client, scheduler=scheduler)

    def next_batch(self) -> EventTimeBarBatch:
        selected = self.scheduler.select_next(int(self.config.ticker_group_size))
        if not selected:
            raise StopIteration("No active ticker cursors remain.")
        requests = build_requests(selected, self.config)
        fetch_result = fetch_ticker_blocks_profiled(self.bytes_client, self.config, requests)
        batch = build_event_time_bar_batch(
            rows_by_ticker=fetch_result.rows_by_ticker,
            requests=requests,
            config=self.config,
            provider_name="clickhouse_ticker_block",
            batch_id=self.batch_id,
        )
        if batch.header_uint8.shape[0] == 0:
            raise RuntimeError(f"Ticker block produced no usable samples; rejects={batch.reject_counts}")
        completed = [request for request in requests if request.ticker in fetch_result.rows_by_ticker]
        self.scheduler.update_after_success(completed)
        if self.config.state_path is not None:
            self.scheduler.save(self.config.state_path)
        self.batch_id += 1
        return batch


@dataclass(slots=True)
class TickerEpochScheduler:
    """Without-replacement ticker scheduler with persisted ordinal cursors."""

    cursors: dict[str, TickerCursor]
    ticker_epoch: list[str]
    epoch_position: int = 0
    ticker_epoch_id: int = 0
    seed: int = 17

    @classmethod
    def from_cursors(cls, cursors: Iterable[TickerCursor], *, seed: int = 17, bucket_count: int = 4) -> "TickerEpochScheduler":
        cursor_map = {cursor.ticker.upper(): cursor for cursor in cursors}
        scheduler = cls(cursors=cursor_map, ticker_epoch=[], seed=int(seed))
        scheduler._rebalance_buckets(bucket_count=max(1, int(bucket_count)))
        scheduler._reshuffle_epoch()
        return scheduler

    @classmethod
    def load(cls, path: Path) -> "TickerEpochScheduler":
        payload = json.loads(path.read_text(encoding="utf-8"))
        cursors = {
            ticker: TickerCursor(
                ticker=ticker,
                first_ordinal=int(row["first_ordinal"]),
                next_origin_ordinal=int(row["next_origin_ordinal"]),
                last_ordinal=int(row["last_ordinal"]),
                event_count=int(row["event_count"]),
                bucket=int(row.get("bucket", 0)),
            )
            for ticker, row in payload["cursors"].items()
        }
        return cls(
            cursors=cursors,
            ticker_epoch=[str(value).upper() for value in payload["ticker_epoch"]],
            epoch_position=int(payload["epoch_position"]),
            ticker_epoch_id=int(payload["ticker_epoch_id"]),
            seed=int(payload["seed"]),
        )

    def save(self, path: Path) -> None:
        payload = {
            "seed": int(self.seed),
            "ticker_epoch_id": int(self.ticker_epoch_id),
            "epoch_position": int(self.epoch_position),
            "ticker_epoch": list(self.ticker_epoch),
            "cursors": {ticker: asdict(cursor) for ticker, cursor in sorted(self.cursors.items())},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def select_next(self, count: int) -> list[TickerCursor]:
        selected: list[TickerCursor] = []
        target = max(1, int(count))
        if self.epoch_position >= len(self.ticker_epoch):
            self.ticker_epoch_id += 1
            self.epoch_position = 0
            self._reshuffle_epoch()
        while len(selected) < target and self.epoch_position < len(self.ticker_epoch):
            ticker = self.ticker_epoch[self.epoch_position]
            self.epoch_position += 1
            cursor = self.cursors.get(ticker)
            if cursor is not None and cursor.active:
                selected.append(cursor)
        return selected

    def update_after_success(self, requests: Iterable[TickerBlockRequest]) -> None:
        for request in requests:
            ticker = request.ticker.upper()
            cursor = self.cursors[ticker]
            self.cursors[ticker] = TickerCursor(
                ticker=ticker,
                first_ordinal=cursor.first_ordinal,
                next_origin_ordinal=int(request.origin_end_ordinal) + 1,
                last_ordinal=cursor.last_ordinal,
                event_count=cursor.event_count,
                bucket=cursor.bucket,
            )

    def _rebalance_buckets(self, *, bucket_count: int) -> None:
        active = sorted(self.cursors.values(), key=lambda cursor: cursor.event_count)
        if not active:
            return
        total = len(active)
        updated: dict[str, TickerCursor] = {}
        for rank, cursor in enumerate(active):
            bucket = min(bucket_count - 1, int(rank * bucket_count / max(1, total)))
            updated[cursor.ticker] = TickerCursor(
                ticker=cursor.ticker,
                first_ordinal=cursor.first_ordinal,
                next_origin_ordinal=cursor.next_origin_ordinal,
                last_ordinal=cursor.last_ordinal,
                event_count=cursor.event_count,
                bucket=bucket,
            )
        self.cursors.update(updated)

    def _reshuffle_epoch(self) -> None:
        rng = random.Random(int(self.seed) + int(self.ticker_epoch_id))
        buckets: dict[int, list[str]] = {}
        for cursor in self.cursors.values():
            if cursor.active:
                buckets.setdefault(cursor.bucket, []).append(cursor.ticker)
        for values in buckets.values():
            rng.shuffle(values)
        interleaved: list[str] = []
        bucket_ids = sorted(buckets)
        while any(buckets.values()):
            for bucket in bucket_ids:
                values = buckets[bucket]
                if values:
                    interleaved.append(values.pop())
        self.ticker_epoch = interleaved


def load_ticker_cursors_from_index(
    client: ClickHouseHttpClient,
    config: TickerBlockDataConfig,
    *,
    limit: int = 0,
) -> list[TickerCursor]:
    table = f"{quote_ident(config.database)}.{quote_ident(config.index_table)}"
    limit_sql = f" LIMIT {int(limit)}" if int(limit) > 0 else ""
    # The index table is built from the event table and is much cheaper than a
    # fresh GROUP BY over all events. It is still equivalent for cursor startup.
    query = f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal,
    split_event_count
FROM {table}
WHERE split_event_count >= {int(config.events_per_chunk) + int(config.future_tail_events)}
ORDER BY ticker
{limit_sql}
FORMAT TSV
"""
    cursors: list[TickerCursor] = []
    for line in client.execute(query).splitlines():
        if not line:
            continue
        ticker, first_ordinal, max_valid_ordinal, event_count = line.split("\t")
        first = int(first_ordinal)
        first_origin = first + int(config.events_per_chunk) - 1
        last_origin = int(max_valid_ordinal) - max(0, int(config.future_tail_events))
        if last_origin < first_origin:
            continue
        cursors.append(
            TickerCursor(
                ticker=ticker.upper(),
                first_ordinal=first,
                next_origin_ordinal=first_origin,
                last_ordinal=last_origin,
                event_count=int(event_count),
            )
        )
    if not cursors:
        raise RuntimeError(f"No eligible cursors loaded from {table}")
    return cursors


def build_requests(cursors: Iterable[TickerCursor], config: TickerBlockDataConfig) -> list[TickerBlockRequest]:
    requests: list[TickerBlockRequest] = []
    context = int(config.events_per_chunk)
    block_origins = max(1, int(config.events_per_ticker_block))
    future_tail = max(0, int(config.future_tail_events))
    for cursor in cursors:
        origin_start = max(int(cursor.first_ordinal) + context - 1, int(cursor.next_origin_ordinal))
        origin_end = min(int(cursor.last_ordinal), origin_start + block_origins - 1)
        if origin_end < origin_start:
            continue
        low = origin_start - context + 1
        high = min(int(cursor.last_ordinal) + future_tail, origin_end + future_tail)
        requests.append(
            TickerBlockRequest(
                ticker=cursor.ticker.upper(),
                low_ordinal=low,
                high_ordinal=high,
                origin_start_ordinal=origin_start,
                origin_end_ordinal=origin_end,
                expected_rows=high - low + 1,
            )
        )
    return requests


def fetch_ticker_blocks(
    client: PersistentClickHouseBytesClient,
    config: TickerBlockDataConfig,
    requests: list[TickerBlockRequest],
) -> dict[str, np.ndarray]:
    return fetch_ticker_blocks_profiled(client, config, requests).rows_by_ticker


def fetch_ticker_blocks_profiled(
    client: PersistentClickHouseBytesClient,
    config: TickerBlockDataConfig,
    requests: list[TickerBlockRequest],
) -> FetchResult:
    spans = [
        EventSpan(
            span_id=index,
            ticker=request.ticker,
            low_ordinal=request.low_ordinal,
            high_ordinal=request.high_ordinal,
            base_origin=request.origin_start_ordinal,
            stride=1,
            origins_per_span=1,
            expected_rows=request.expected_rows,
        )
        for index, request in enumerate(requests)
    ]
    span_config = _span_config_from_ticker_block_config(config)
    started = time.perf_counter()
    rows = fetch_spans(client, span_config, spans)
    fetch_seconds = time.perf_counter() - started
    out: dict[str, np.ndarray] = {}
    if rows.size == 0:
        return FetchResult(rows_by_ticker=out, requests=requests, fetch_seconds=fetch_seconds, rows_returned=0, query_mode="ordinal")
    span_ids = rows["span_id"]
    boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [rows.shape[0]]))
    for start, end in zip(starts, ends):
        request = requests[int(span_ids[start])]
        out[request.ticker] = rows[start:end].copy()
    return FetchResult(rows_by_ticker=out, requests=requests, fetch_seconds=fetch_seconds, rows_returned=int(rows.shape[0]), query_mode="ordinal")


def fetch_ticker_date_blocks_profiled(
    client: PersistentClickHouseBytesClient,
    config: TickerBlockDataConfig,
    tickers: Iterable[str],
    *,
    event_date: str,
) -> FetchResult:
    ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
    if not ticker_tuple:
        return FetchResult(rows_by_ticker={}, requests=[], fetch_seconds=0.0, rows_returned=0, query_mode="date")
    started = time.perf_counter()
    payload = client.execute_bytes(date_block_query(config, ticker_tuple, event_date=event_date))
    fetch_seconds = time.perf_counter() - started
    if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
        raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by row size {EVENT_ROW_DTYPE.itemsize}")
    rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE)
    rows_by_ticker: dict[str, np.ndarray] = {}
    requests: list[TickerBlockRequest] = []
    if rows.size == 0:
        return FetchResult(rows_by_ticker=rows_by_ticker, requests=requests, fetch_seconds=fetch_seconds, rows_returned=0, query_mode="date")
    span_ids = rows["span_id"]
    boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [rows.shape[0]]))
    context = int(config.events_per_chunk)
    future_tail = max(0, int(config.future_tail_events))
    for start, end in zip(starts, ends):
        span_id = int(span_ids[start])
        ticker = ticker_tuple[span_id]
        ticker_rows = rows[start:end].copy()
        low = int(ticker_rows["ordinal"][0])
        high = int(ticker_rows["ordinal"][-1])
        origin_start = low + context - 1
        origin_end = high - future_tail
        if origin_end < origin_start:
            continue
        rows_by_ticker[ticker] = ticker_rows
        requests.append(
            TickerBlockRequest(
                ticker=ticker,
                low_ordinal=low,
                high_ordinal=high,
                origin_start_ordinal=origin_start,
                origin_end_ordinal=origin_end,
                expected_rows=high - low + 1,
            )
        )
    return FetchResult(
        rows_by_ticker=rows_by_ticker,
        requests=requests,
        fetch_seconds=fetch_seconds,
        rows_returned=int(rows.shape[0]),
        query_mode="date",
    )


def date_block_query(config: TickerBlockDataConfig, tickers: tuple[str, ...], *, event_date: str) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    parts = [
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
        for index, ticker in enumerate(tickers)
    ]
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
{ticker_block_query_settings(config)}
FORMAT RowBinary
"""


def ticker_block_query_settings(config: TickerBlockDataConfig) -> str:
    settings: list[str] = []
    if int(config.max_threads) > 0:
        settings.append(f"max_threads = {int(config.max_threads)}")
    if str(config.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def build_event_time_bar_batch(
    rows_by_ticker: dict[str, np.ndarray],
    requests: list[TickerBlockRequest],
    config: TickerBlockDataConfig,
    *,
    provider_name: str = "ticker_block",
    batch_id: int = 0,
) -> EventTimeBarBatch:
    profiler = DataPrepProfiler(provider_name, batch_id=int(batch_id), enabled=True)
    reject_counts: dict[str, int] = {}
    headers: list[np.ndarray] = []
    event_bytes: list[np.ndarray] = []
    tickers: list[str] = []
    origin_ordinals: list[int] = []
    origin_timestamps_us: list[int] = []
    label_chunks: list[dict[str, np.ndarray]] = []

    if config.assemble_polars_table:
        with profiler.stage("polars_assemble_sort"):
            assemble_polars_event_table(rows_by_ticker)

    with profiler.stage("encode_windows"):
        for request in requests:
            rows = rows_by_ticker.get(request.ticker)
            if rows is None:
                reject_counts["missing_ticker_rows"] = reject_counts.get("missing_ticker_rows", 0) + 1
                continue
            sample = build_samples_for_ticker(rows, request, config)
            reject_counts.update({key: reject_counts.get(key, 0) + value for key, value in sample.reject_counts.items()})
            if sample.header_uint8.size == 0:
                continue
            headers.append(sample.header_uint8)
            event_bytes.append(sample.events_uint8)
            tickers.extend([request.ticker] * sample.header_uint8.shape[0])
            origin_ordinals.extend(sample.origin_ordinal.tolist())
            origin_timestamps_us.extend(sample.origin_timestamp_us.tolist())
            label_chunks.append(sample.labels)
            profiler.profile.rows_read += int(rows.shape[0])
            profiler.profile.valid_rows += int(rows.shape[0])
            profiler.profile.chunks_created += int(sample.header_uint8.shape[0])

    if headers:
        header_array = np.concatenate(headers, axis=0).astype(np.uint8, copy=False)
        event_array = np.concatenate(event_bytes, axis=0).astype(np.uint8, copy=False)
    else:
        header_array = np.zeros((0, HEADER_BYTES), dtype=np.uint8)
        event_array = np.zeros((0, int(config.events_per_chunk), EVENT_BYTES), dtype=np.uint8)

    with profiler.stage("label_pack"):
        labels = merge_label_chunks(label_chunks)

    profile = profiler.finish()
    profile.samples_created = int(header_array.shape[0])
    profile.labels_created = int(sum(value.shape[1] if value.ndim > 1 else 1 for value in labels.values()))
    profile.output_batches_created = 1
    return EventTimeBarBatch(
        header_uint8=header_array,
        events_uint8=event_array,
        ticker=np.asarray(tickers, dtype=object),
        origin_ordinal=np.asarray(origin_ordinals, dtype=np.int64),
        origin_timestamp_us=np.asarray(origin_timestamps_us, dtype=np.int64),
        labels=labels,
        profile=profile,
        reject_counts=reject_counts,
    )


def assemble_polars_event_table(rows_by_ticker: dict[str, np.ndarray]):
    """Build a single sorted Polars table for profiling block-level strategies."""

    try:
        import polars as pl
    except ModuleNotFoundError:
        return None
    frames = []
    for ticker, rows in rows_by_ticker.items():
        if rows.size == 0:
            continue
        frames.append(
            pl.DataFrame(
                {
                    "ticker": [ticker] * int(rows.shape[0]),
                    "ordinal": rows["ordinal"],
                    "event_type": rows["event_type"],
                    "sip_timestamp_us": rows["sip_timestamp_us"],
                    "price_primary_int": rows["price_primary_int"],
                    "price_secondary_int": rows["price_secondary_int"],
                    "size_primary": rows["size_primary"],
                    "size_secondary": rows["size_secondary"],
                    "exchange_primary": rows["exchange_primary"],
                    "exchange_secondary": rows["exchange_secondary"],
                    "event_flags": rows["event_flags"],
                    "conditions_packed": rows["conditions_packed"],
                }
            )
        )
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical").sort(["ticker", "ordinal"])


@dataclass(frozen=True, slots=True)
class _TickerSamples:
    header_uint8: np.ndarray
    events_uint8: np.ndarray
    origin_ordinal: np.ndarray
    origin_timestamp_us: np.ndarray
    labels: dict[str, np.ndarray]
    reject_counts: dict[str, int]


def build_samples_for_ticker(rows: np.ndarray, request: TickerBlockRequest, config: TickerBlockDataConfig) -> _TickerSamples:
    previous_sip_us: int | None = None
    if rows.shape[0] == int(request.expected_rows) + 1 and int(rows["ordinal"][0]) == int(request.low_ordinal) - 1:
        previous_sip_us = int(rows["sip_timestamp_us"][0])
        rows = rows[1:]
    if rows.shape[0] != int(request.expected_rows):
        return _empty_ticker_samples(config, {"row_count_mismatch": 1})
    if int(rows["ordinal"][0]) != int(request.low_ordinal) or int(rows["ordinal"][-1]) != int(request.high_ordinal):
        return _empty_ticker_samples(config, {"ordinal_range_mismatch": 1})
    if np.any(rows["ordinal"][1:] != rows["ordinal"][:-1] + 1):
        return _empty_ticker_samples(config, {"ordinal_gap": 1})

    context = int(config.events_per_chunk)
    stride = max(1, int(config.sample_stride_events))
    first_offset = int(request.origin_start_ordinal) - int(request.low_ordinal)
    last_offset = int(request.origin_end_ordinal) - int(request.low_ordinal)
    origin_offsets = np.arange(first_offset, last_offset + 1, stride, dtype=np.int64)
    if int(config.max_samples_per_ticker) > 0 and origin_offsets.size > int(config.max_samples_per_ticker):
        origin_offsets = origin_offsets[: int(config.max_samples_per_ticker)]

    headers = np.zeros((origin_offsets.size, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((origin_offsets.size, context, EVENT_BYTES), dtype=np.uint8)
    keep = np.ones((origin_offsets.size,), dtype=np.bool_)
    reject_counts: dict[str, int] = {}
    for sample_index, origin_offset in enumerate(origin_offsets.tolist()):
        start = int(origin_offset) - context + 1
        end = int(origin_offset) + 1
        window_previous_sip_us = int(rows["sip_timestamp_us"][start - 1]) if start > 0 else previous_sip_us
        encoded = encode_unified_event_window(rows[start:end], previous_sip_us=window_previous_sip_us)
        if isinstance(encoded, str):
            keep[sample_index] = False
            reject_counts[encoded] = reject_counts.get(encoded, 0) + 1
            continue
        headers[sample_index], events[sample_index] = encoded

    origin_offsets = origin_offsets[keep]
    headers = headers[keep]
    events = events[keep]
    labels = build_future_time_bar_labels(rows=rows, origin_offsets=origin_offsets, horizons=config.horizons)
    return _TickerSamples(
        header_uint8=headers,
        events_uint8=events,
        origin_ordinal=rows["ordinal"][origin_offsets].astype(np.int64, copy=True),
        origin_timestamp_us=rows["sip_timestamp_us"][origin_offsets].astype(np.int64, copy=True),
        labels=labels,
        reject_counts=reject_counts,
    )


def build_future_time_bar_labels(
    *,
    rows: np.ndarray,
    origin_offsets: np.ndarray,
    horizons: tuple[TimeBarHorizon, ...],
    state_cache: MutableMapping[int, FutureTimeBarLabelState] | None = None,
    state_cache_lock: threading.Lock | None = None,
) -> dict[str, np.ndarray]:
    origin_ts = rows["sip_timestamp_us"][origin_offsets].astype(np.int64, copy=False)
    labels: dict[str, np.ndarray] = {}
    state = _future_time_bar_label_state(rows, state_cache=state_cache, state_cache_lock=state_cache_lock)
    if state.trade_ts.size == 0:
        for horizon in horizons:
            _add_empty_bar_labels(labels, horizon.name, origin_ts.shape[0])
        return labels

    session_end_us = _us_eastern_session_end_us(origin_ts)
    for horizon in horizons:
        start_idx = np.searchsorted(state.trade_ts, origin_ts, side="right")
        horizon_end_us = origin_ts + int(horizon.microseconds)
        capped_end_us = np.minimum(horizon_end_us, session_end_us)
        end_idx = np.searchsorted(state.trade_ts, capped_end_us, side="right")
        end_idx = np.maximum(start_idx, end_idx)
        count = np.maximum(0, end_idx - start_idx).astype(np.uint32)
        has_trade = count > 0
        open_price = np.zeros((origin_ts.shape[0],), dtype=np.float32)
        close_price = np.zeros((origin_ts.shape[0],), dtype=np.float32)
        high_price = np.zeros((origin_ts.shape[0],), dtype=np.float32)
        low_price = np.zeros((origin_ts.shape[0],), dtype=np.float32)
        volume = (state.volume_prefix[end_idx] - state.volume_prefix[start_idx]).astype(np.float32)
        if np.any(has_trade):
            valid_start = start_idx[has_trade]
            valid_end = end_idx[has_trade]
            open_price[has_trade] = state.trade_price[valid_start].astype(np.float32, copy=False)
            close_price[has_trade] = state.trade_price[valid_end - 1].astype(np.float32, copy=False)
            if state.price_rmq is not None:
                low, high = state.price_rmq.query(valid_start, valid_end)
                high_price[has_trade] = high.astype(np.float32, copy=False)
                low_price[has_trade] = low.astype(np.float32, copy=False)
        prefix = f"future_bar_{horizon.name}"
        labels[f"{prefix}_has_trade"] = has_trade.astype(np.uint8)
        labels[f"{prefix}_open"] = open_price
        labels[f"{prefix}_high"] = high_price
        labels[f"{prefix}_low"] = low_price
        labels[f"{prefix}_close"] = close_price
        labels[f"{prefix}_volume"] = volume
    return labels


def _us_eastern_session_end_us(timestamps_us: np.ndarray) -> np.ndarray:
    """Return the 20:00 America/New_York session end for each UTC timestamp.

    This keeps intraday future bars inside the same extended-hours trading
    session without a per-row timezone conversion in the hot path.
    """

    values = np.asarray(timestamps_us, dtype=np.int64)
    if values.size == 0:
        return np.zeros(values.shape, dtype=np.int64)
    day_us = 86_400_000_000
    hour_us = 3_600_000_000
    est_offset_us = -5 * hour_us
    edt_offset_us = -4 * hour_us
    dst_mask = _us_eastern_dst_mask_utc(values)
    local_us = values + np.where(dst_mask, edt_offset_us, est_offset_us)
    local_day = np.floor_divide(local_us, day_us)
    local_noon_us = local_day * day_us + 12 * hour_us
    close_dst_mask = _us_eastern_dst_mask_local_noon(local_noon_us)
    close_offset_us = np.where(close_dst_mask, edt_offset_us, est_offset_us)
    return local_day * day_us + 20 * hour_us - close_offset_us


def _us_eastern_dst_mask_utc(timestamps_us: np.ndarray) -> np.ndarray:
    values = np.asarray(timestamps_us, dtype=np.int64)
    years = _utc_years_from_timestamp_us(values)
    out = np.zeros(values.shape, dtype=np.bool_)
    for year in np.unique(years):
        year_int = int(year)
        start = _timestamp_us_utc(_nth_weekday(year_int, 3, 6, 2), hour=7)
        end = _timestamp_us_utc(_nth_weekday(year_int, 11, 6, 1), hour=6)
        mask = years == year_int
        out[mask] = (values[mask] >= start) & (values[mask] < end)
    return out


def _us_eastern_dst_mask_local_noon(local_noon_us: np.ndarray) -> np.ndarray:
    values = np.asarray(local_noon_us, dtype=np.int64)
    years = _utc_years_from_timestamp_us(values)
    out = np.zeros(values.shape, dtype=np.bool_)
    for year in np.unique(years):
        year_int = int(year)
        start_local = _timestamp_us_utc(_nth_weekday(year_int, 3, 6, 2), hour=2)
        end_local = _timestamp_us_utc(_nth_weekday(year_int, 11, 6, 1), hour=2)
        mask = years == year_int
        out[mask] = (values[mask] >= start_local) & (values[mask] < end_local)
    return out


def _utc_years_from_timestamp_us(timestamps_us: np.ndarray) -> np.ndarray:
    seconds = np.floor_divide(np.asarray(timestamps_us, dtype=np.int64), 1_000_000)
    days = np.floor_divide(seconds, 86_400)
    years = np.empty(days.shape, dtype=np.int32)
    for day in np.unique(days):
        years[days == day] = dt.datetime.fromtimestamp(int(day) * 86_400, tz=dt.timezone.utc).year
    return years


def _timestamp_us_utc(day: dt.date, *, hour: int) -> int:
    return int(dt.datetime.combine(day, dt.time(hour=int(hour)), tzinfo=dt.timezone.utc).timestamp() * 1_000_000)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(int(year), int(month), 1)
    delta = (int(weekday) - first.weekday()) % 7
    return first + dt.timedelta(days=delta + 7 * (int(n) - 1))


def _future_time_bar_label_state(
    rows: np.ndarray,
    *,
    state_cache: MutableMapping[int, FutureTimeBarLabelState] | None = None,
    state_cache_lock: threading.Lock | None = None,
) -> FutureTimeBarLabelState:
    cache_key = id(rows)
    if state_cache is not None:
        if state_cache_lock is not None:
            with state_cache_lock:
                cached = state_cache.get(cache_key)
                if cached is not None:
                    return cached
            state = _build_future_time_bar_label_state(rows)
            with state_cache_lock:
                cached = state_cache.get(cache_key)
                if cached is not None:
                    return cached
                state_cache[cache_key] = state
                return state
        cached = state_cache.get(cache_key)
        if cached is not None:
            return cached
    state = _build_future_time_bar_label_state(rows)
    if state_cache is not None:
        state_cache[cache_key] = state
    return state


def _build_future_time_bar_label_state(rows: np.ndarray) -> FutureTimeBarLabelState:
    trade_mask = rows["event_type"].astype(np.uint8, copy=False) == TRADE_EVENT_TYPE
    trade_rows = rows[trade_mask]
    trade_ts = trade_rows["sip_timestamp_us"].astype(np.int64, copy=False)
    trade_price = _decode_trade_price(trade_rows)
    trade_size = trade_rows["size_primary"].astype(np.float64, copy=False)
    volume_prefix = np.concatenate(([0.0], np.cumsum(np.maximum(0.0, trade_size), dtype=np.float64)))
    return FutureTimeBarLabelState(
        trade_ts=trade_ts,
        trade_price=trade_price,
        trade_size=trade_size,
        volume_prefix=volume_prefix,
        price_rmq=_RangeMinMax(trade_price) if trade_price.size else None,
    )


class _RangeMinMax:
    def __init__(self, values: np.ndarray) -> None:
        arr = values.astype(np.float64, copy=False)
        self.values = arr
        self.mins = [arr]
        self.maxs = [arr]
        length = arr.shape[0]
        width = 1
        while width * 2 <= length:
            prev_min = self.mins[-1]
            prev_max = self.maxs[-1]
            next_len = length - width * 2 + 1
            self.mins.append(np.minimum(prev_min[:next_len], prev_min[width : width + next_len]))
            self.maxs.append(np.maximum(prev_max[:next_len], prev_max[width : width + next_len]))
            width *= 2

    def query(self, start: np.ndarray, end: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        length = np.maximum(1, end - start)
        k = np.floor(np.log2(length)).astype(np.int64)
        out_min = np.empty(start.shape, dtype=np.float64)
        out_max = np.empty(start.shape, dtype=np.float64)
        for level in np.unique(k):
            mask = k == level
            width = 1 << int(level)
            left = start[mask]
            right = end[mask] - width
            out_min[mask] = np.minimum(self.mins[int(level)][left], self.mins[int(level)][right])
            out_max[mask] = np.maximum(self.maxs[int(level)][left], self.maxs[int(level)][right])
        return out_min, out_max


def _decode_trade_price(rows: np.ndarray) -> np.ndarray:
    scale = rows["event_flags"].astype(np.uint8, copy=False) & 1
    denominator = np.where(scale == 1, 10000.0, 100.0)
    return rows["price_primary_int"].astype(np.float64, copy=False) / denominator


def _add_empty_bar_labels(labels: dict[str, np.ndarray], name: str, count: int) -> None:
    prefix = f"future_bar_{name}"
    labels[f"{prefix}_has_trade"] = np.zeros((count,), dtype=np.uint8)
    labels[f"{prefix}_open"] = np.zeros((count,), dtype=np.float32)
    labels[f"{prefix}_high"] = np.zeros((count,), dtype=np.float32)
    labels[f"{prefix}_low"] = np.zeros((count,), dtype=np.float32)
    labels[f"{prefix}_close"] = np.zeros((count,), dtype=np.float32)
    labels[f"{prefix}_volume"] = np.zeros((count,), dtype=np.float32)


def merge_label_chunks(chunks: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    if not chunks:
        return {}
    keys = sorted({key for chunk in chunks for key in chunk})
    merged: dict[str, np.ndarray] = {}
    for key in keys:
        values = [chunk[key] for chunk in chunks if key in chunk]
        if values:
            merged[key] = np.concatenate(values, axis=0)
    return merged


def _empty_ticker_samples(config: TickerBlockDataConfig, reject_counts: dict[str, int]) -> _TickerSamples:
    return _TickerSamples(
        header_uint8=np.zeros((0, HEADER_BYTES), dtype=np.uint8),
        events_uint8=np.zeros((0, int(config.events_per_chunk), EVENT_BYTES), dtype=np.uint8),
        origin_ordinal=np.zeros((0,), dtype=np.int64),
        origin_timestamp_us=np.zeros((0,), dtype=np.int64),
        labels={},
        reject_counts=reject_counts,
    )


def _span_config_from_ticker_block_config(config: TickerBlockDataConfig):
    from research.mlops.clickhouse_events import ClickHouseEventsDataConfig

    return ClickHouseEventsDataConfig(
        database=config.database,
        events_table=config.events_table,
        index_table=config.index_table,
        events_per_chunk=config.events_per_chunk,
        max_threads=config.max_threads,
        max_memory_usage=config.max_memory_usage,
        past_span_events=config.events_per_chunk,
        future_span_events=config.future_tail_events,
        return_torch=False,
    )


def profile_batch_summary(batch: EventTimeBarBatch) -> str:
    metrics = batch.profile.to_metrics(prefix="ticker_block")
    return (
        f"samples={batch.header_uint8.shape[0]:,} rows={int(metrics['ticker_block/rows_read']):,} "
        f"seconds={metrics['ticker_block/total_seconds']:.3f} "
        f"samples_per_sec={metrics['ticker_block/samples_per_second']:.1f} "
        f"rejects={batch.reject_counts}"
    )


def sample_bytes_per_row(config: TickerBlockDataConfig) -> int:
    return HEADER_BYTES + int(config.events_per_chunk) * EVENT_BYTES


def make_synthetic_event_rows(count: int, low_ordinal: int) -> np.ndarray:
    """Generate compact event-table-like rows for local provider tests."""

    rows = np.zeros((int(count),), dtype=EVENT_ROW_DTYPE)
    ordinal = np.arange(int(low_ordinal), int(low_ordinal) + int(count), dtype=np.uint64)
    rows["ordinal"] = ordinal
    rows["sip_timestamp_us"] = 1_700_000_000_000_000 + ordinal.astype(np.uint64) * 1_000
    rows["event_type"] = QUOTE_EVENT_TYPE
    trade_mask = (ordinal % 5) == 0
    rows["event_type"][trade_mask] = TRADE_EVENT_TYPE
    base_price = 10_000 + (ordinal % 200).astype(np.uint32)
    rows["price_primary_int"] = base_price
    rows["price_secondary_int"] = base_price - 1
    rows["size_primary"] = 100.0 + (ordinal % 50).astype(np.float32)
    rows["size_secondary"] = 100.0
    rows["exchange_primary"] = 1
    rows["exchange_secondary"] = 1
    rows["event_flags"] = 0x04
    rows["conditions_packed"] = 0
    return rows
