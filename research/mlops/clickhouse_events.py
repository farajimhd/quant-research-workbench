from __future__ import annotations

import http.client
import math
import os
import random
import statistics
import time
from dataclasses import dataclass
from typing import Iterator
from urllib import parse

import numpy as np
import torch
from torch.utils.data import IterableDataset, get_worker_info

from research.mlops.clickhouse_ingest_sip_flatfiles import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES, QUOTE_EVENT_TYPE, TRADE_EVENT_TYPE


DEFAULT_CLICKHOUSE_URL = "http://localhost:18123"
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"
DEFAULT_CONTEXT_EVENTS = 128
DEFAULT_ORIGINS_PER_SPAN = 32
DEFAULT_MIN_ORIGIN_STRIDE = 1
DEFAULT_MAX_ORIGIN_STRIDE = 16
DEFAULT_QUERY_BUNDLE_SPANS = 64

EVENT_ROW_DTYPE = np.dtype(
    [
        ("span_id", "<u4"),
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
class ClickHouseEventsDataConfig:
    clickhouse_url: str = ""
    user: str = ""
    password: str = ""
    database: str = DEFAULT_DATABASE
    events_table: str = DEFAULT_EVENTS_TABLE
    train_index_table: str = DEFAULT_TRAIN_INDEX_TABLE
    validation_index_table: str = DEFAULT_VALIDATION_INDEX_TABLE
    index_table: str = ""
    split: str = "train"
    tickers: tuple[str, ...] = ("ALL",)
    events_per_chunk: int = DEFAULT_CONTEXT_EVENTS
    batch_size: int = 4096
    num_spans: int = 128
    origins_per_span: int = DEFAULT_ORIGINS_PER_SPAN
    min_origin_stride: int = DEFAULT_MIN_ORIGIN_STRIDE
    max_origin_stride: int = DEFAULT_MAX_ORIGIN_STRIDE
    query_bundle_spans: int = DEFAULT_QUERY_BUNDLE_SPANS
    max_threads: int = 8
    max_memory_usage: str = "80G"
    seed: int = 17
    max_index_rows: int = 0
    max_span_attempt_multiplier: int = 10
    strict_lossless: bool = True


@dataclass(frozen=True, slots=True)
class EventIndexRow:
    ticker: str
    first_ordinal: int
    max_valid_ordinal: int
    event_count: int


@dataclass(frozen=True, slots=True)
class EventSpan:
    span_id: int
    ticker: str
    low_ordinal: int
    high_ordinal: int
    base_origin: int
    stride: int
    origins_per_span: int
    expected_rows: int


class PersistentClickHouseBytesClient:
    def __init__(self, base_url: str, user: str, password: str) -> None:
        parsed = parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported ClickHouse URL scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError(f"Invalid ClickHouse URL: {base_url!r}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.path_prefix = parsed.path.rstrip("/")
        self.user = user
        self.password = password
        self._conn: http.client.HTTPConnection | http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> http.client.HTTPConnection | http.client.HTTPSConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
            self._conn = cls(self.host, self.port, timeout=900)
        return self._conn

    def execute_bytes(self, sql: str) -> bytes:
        path = (self.path_prefix or "") + "/"
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.user:
            headers["X-ClickHouse-User"] = self.user
        if self.password:
            headers["X-ClickHouse-Key"] = self.password
        body = sql.encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                conn = self._connection()
                conn.request("POST", path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                if response.status >= 400:
                    text = payload.decode("utf-8", errors="replace")
                    raise RuntimeError(f"ClickHouse HTTP {response.status} {response.reason}: {text}")
                return payload
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                self.close()
                if attempt == 1:
                    raise
        raise RuntimeError(f"ClickHouse request failed: {last_error!r}")


class ClickHouseEventsChunkIterableDataset(IterableDataset):
    def __init__(self, config: ClickHouseEventsDataConfig, *, index_rows: list[EventIndexRow] | None = None) -> None:
        super().__init__()
        self.config = normalized_config(config)
        self.index_rows = index_rows

    def __iter__(self) -> Iterator[dict[str, object]]:
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.config.seed + worker_id)
        text_client = ClickHouseHttpClient(self.config.clickhouse_url, self.config.user, self.config.password)
        index_rows = self.index_rows or load_event_index_rows(text_client, self.config)
        bytes_client = PersistentClickHouseBytesClient(self.config.clickhouse_url, self.config.user, self.config.password)
        try:
            while True:
                yield build_clickhouse_events_batch(index_rows, self.config, bytes_client, rng)
        finally:
            bytes_client.close()


def normalized_config(config: ClickHouseEventsDataConfig) -> ClickHouseEventsDataConfig:
    user = config.user or default_clickhouse_user()
    password = config.password or default_clickhouse_password()
    url = config.clickhouse_url or os.environ.get("CLICKHOUSE_URL") or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL") or DEFAULT_CLICKHOUSE_URL
    num_spans = int(config.num_spans)
    origins_per_span = int(config.origins_per_span)
    batch_size = int(config.batch_size)
    if num_spans <= 0:
        if batch_size % origins_per_span != 0:
            raise ValueError(f"batch_size={batch_size} must be divisible by origins_per_span={origins_per_span}")
        num_spans = batch_size // origins_per_span
    expected_batch = num_spans * origins_per_span
    if expected_batch != batch_size:
        raise ValueError(f"batch_size must equal num_spans * origins_per_span; got {batch_size} != {num_spans} * {origins_per_span}")
    if config.events_per_chunk != DEFAULT_CONTEXT_EVENTS:
        raise ValueError(f"ClickHouse events loader currently expects events_per_chunk={DEFAULT_CONTEXT_EVENTS}; got {config.events_per_chunk}")
    if config.min_origin_stride < 1 or config.max_origin_stride < config.min_origin_stride:
        raise ValueError("origin stride bounds must satisfy 1 <= min_origin_stride <= max_origin_stride")
    return ClickHouseEventsDataConfig(
        clickhouse_url=url,
        user=user,
        password=password,
        database=config.database,
        events_table=config.events_table,
        train_index_table=config.train_index_table,
        validation_index_table=config.validation_index_table,
        index_table=config.index_table,
        split=config.split,
        tickers=config.tickers,
        events_per_chunk=config.events_per_chunk,
        batch_size=batch_size,
        num_spans=num_spans,
        origins_per_span=origins_per_span,
        min_origin_stride=config.min_origin_stride,
        max_origin_stride=config.max_origin_stride,
        query_bundle_spans=config.query_bundle_spans,
        max_threads=config.max_threads,
        max_memory_usage=config.max_memory_usage,
        seed=config.seed,
        max_index_rows=config.max_index_rows,
        max_span_attempt_multiplier=config.max_span_attempt_multiplier,
        strict_lossless=config.strict_lossless,
    )


def load_event_index_rows(client: ClickHouseHttpClient, config: ClickHouseEventsDataConfig) -> list[EventIndexRow]:
    table = config.index_table or (config.validation_index_table if config.split == "validation" else config.train_index_table)
    ticker_filter = ""
    tickers = tuple(value.upper() for value in config.tickers if value)
    if tickers and tickers != ("ALL",) and "*" not in tickers:
        ticker_filter = "AND ticker IN (" + ", ".join(sql_string(value) for value in tickers) + ")"
    limit = f" LIMIT {int(config.max_index_rows)}" if config.max_index_rows > 0 else ""
    query = f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal,
    split_event_count
FROM {quote_ident(config.database)}.{quote_ident(table)}
WHERE split_event_count >= {int(config.events_per_chunk)}
  AND max_valid_ordinal >= first_ordinal + {int(config.events_per_chunk) - 1}
  {ticker_filter}
ORDER BY ticker
{limit}
FORMAT TSV
"""
    rows: list[EventIndexRow] = []
    for line in client.execute(query).splitlines():
        if not line:
            continue
        ticker, first_ordinal, max_valid_ordinal, event_count = line.split("\t")
        rows.append(EventIndexRow(ticker=ticker, first_ordinal=int(first_ordinal), max_valid_ordinal=int(max_valid_ordinal), event_count=int(event_count)))
    if not rows:
        raise RuntimeError(f"No eligible rows found in {config.database}.{table}")
    return rows


def build_clickhouse_events_batch(
    index_rows: list[EventIndexRow],
    config: ClickHouseEventsDataConfig,
    client: PersistentClickHouseBytesClient,
    rng: random.Random,
) -> dict[str, object]:
    batch_started = time.perf_counter()
    headers = np.zeros((config.batch_size, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((config.batch_size, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    origin_ts = np.zeros((config.batch_size,), dtype=np.int64)
    tickers: list[str] = []
    filled = 0
    spans_attempted = 0
    spans_accepted = 0
    spans_rejected = 0
    rows_received = 0
    rows_expected = 0
    sample_seconds = 0.0
    query_seconds = 0.0
    parse_seconds = 0.0
    encode_seconds = 0.0
    query_count = 0
    stride_values: list[int] = []
    reject_counts: dict[str, int] = {}
    max_span_attempts = max(config.num_spans, config.num_spans * max(1, config.max_span_attempt_multiplier))
    while filled < config.batch_size and spans_attempted < max_span_attempts:
        remaining_samples = config.batch_size - filled
        remaining_spans = math.ceil(remaining_samples / config.origins_per_span)
        draw_spans = min(config.query_bundle_spans, remaining_spans, max_span_attempts - spans_attempted)
        sample_started = time.perf_counter()
        spans = sample_spans(index_rows, config=config, span_count=draw_spans, rng=rng, start_span_id=0)
        sample_seconds += time.perf_counter() - sample_started
        spans_attempted += len(spans)
        stride_values.extend(span.stride for span in spans)
        rows_expected += sum(span.expected_rows for span in spans)
        fetch_started = time.perf_counter()
        raw_rows = fetch_spans(client, config, spans)
        query_seconds += time.perf_counter() - fetch_started
        parse_started = time.perf_counter()
        rows_received += int(raw_rows.shape[0])
        by_span = split_rows_by_span(raw_rows)
        parse_seconds += time.perf_counter() - parse_started
        encode_started = time.perf_counter()
        for span in spans:
            span_rows = by_span.get(span.span_id)
            if span_rows is None:
                spans_rejected += 1
                reject_counts["missing_span"] = reject_counts.get("missing_span", 0) + 1
                continue
            encoded = encode_span_samples(span_rows, span, config)
            if isinstance(encoded, str):
                spans_rejected += 1
                reject_counts[encoded] = reject_counts.get(encoded, 0) + 1
                continue
            span_headers, span_events, span_origin_ts = encoded
            take = min(span_headers.shape[0], config.batch_size - filled)
            headers[filled : filled + take] = span_headers[:take]
            events[filled : filled + take] = span_events[:take]
            origin_ts[filled : filled + take] = span_origin_ts[:take]
            tickers.extend([span.ticker] * take)
            filled += take
            spans_accepted += 1
            if filled >= config.batch_size:
                break
        encode_seconds += time.perf_counter() - encode_started
        query_count += 1
    if filled < config.batch_size:
        raise RuntimeError(f"Could only build {filled:,}/{config.batch_size:,} samples; rejects={reject_counts}")
    total_seconds = time.perf_counter() - batch_started
    return {
        "header_uint8": torch.from_numpy(headers),
        "events_uint8": torch.from_numpy(events),
        "origin_timestamp_ns": torch.from_numpy(origin_ts),
        "ticker": tickers,
        "row_bytes": HEADER_BYTES + config.events_per_chunk * EVENT_BYTES,
        "events_per_chunk": config.events_per_chunk,
        "profile": {
            "data/batch_build_seconds": total_seconds,
            "data/sample_select_seconds": sample_seconds,
            "data/query_seconds": query_seconds,
            "data/parse_seconds": parse_seconds,
            "data/encode_seconds": encode_seconds,
            "data/query_count": float(query_count),
            "data/spans_attempted": float(spans_attempted),
            "data/spans_accepted": float(spans_accepted),
            "data/spans_rejected": float(spans_rejected),
            "data/rows_expected": float(rows_expected),
            "data/rows_received": float(rows_received),
            "data/batch_samples_per_second": float(config.batch_size) / max(total_seconds, 1e-9),
            "data/origin_stride_min": float(min(stride_values) if stride_values else 0),
            "data/origin_stride_max": float(max(stride_values) if stride_values else 0),
            "data/origin_stride_mean": float(statistics.fmean(stride_values) if stride_values else 0.0),
        },
        "reject_counts": reject_counts,
    }


def sample_spans(
    index_rows: list[EventIndexRow],
    *,
    config: ClickHouseEventsDataConfig,
    span_count: int,
    rng: random.Random,
    start_span_id: int,
) -> list[EventSpan]:
    spans: list[EventSpan] = []
    attempts = 0
    max_attempts = max(span_count * 100, 1000)
    while len(spans) < span_count and attempts < max_attempts:
        attempts += 1
        row = rng.choice(index_rows)
        stride = rng.randint(config.min_origin_stride, config.max_origin_stride)
        high_extra = (config.origins_per_span - 1) * stride
        min_base = row.first_ordinal + config.events_per_chunk - 1
        max_base = row.max_valid_ordinal - high_extra
        if max_base < min_base:
            continue
        base = rng.randint(min_base, max_base)
        low = base - config.events_per_chunk + 1
        high = base + high_extra
        spans.append(
            EventSpan(
                span_id=start_span_id + len(spans),
                ticker=row.ticker,
                low_ordinal=low,
                high_ordinal=high,
                base_origin=base,
                stride=stride,
                origins_per_span=config.origins_per_span,
                expected_rows=high - low + 1,
            )
        )
    if len(spans) < span_count:
        raise RuntimeError(f"Could only sample {len(spans):,}/{span_count:,} spans after {attempts:,} attempts.")
    return spans


def fetch_spans(client: PersistentClickHouseBytesClient, config: ClickHouseEventsDataConfig, spans: list[EventSpan]) -> np.ndarray:
    if not spans:
        return np.empty((0,), dtype=EVENT_ROW_DTYPE)
    payload = client.execute_bytes(span_query(config, spans))
    if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
        raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by row size {EVENT_ROW_DTYPE.itemsize}")
    return np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()


def span_query(config: ClickHouseEventsDataConfig, spans: list[EventSpan]) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    parts = [
        f"""
SELECT
    toUInt32({span.span_id}) AS span_id,
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
PREWHERE ticker = {sql_string(span.ticker)}
  AND ordinal >= {max(0, span.low_ordinal - 1)}
  AND ordinal <= {span.high_ordinal}
""".strip()
        for span in spans
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
{query_settings(config)}
FORMAT RowBinary
"""


def query_settings(config: ClickHouseEventsDataConfig) -> str:
    settings = []
    if config.max_threads > 0:
        settings.append(f"max_threads = {int(config.max_threads)}")
    if str(config.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def split_rows_by_span(rows: np.ndarray) -> dict[int, np.ndarray]:
    if rows.size == 0:
        return {}
    out: dict[int, np.ndarray] = {}
    span_ids = rows["span_id"]
    boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [rows.shape[0]]))
    for start, end in zip(starts, ends):
        out[int(span_ids[start])] = rows[start:end]
    return out


def encode_span_samples(
    rows: np.ndarray,
    span: EventSpan,
    config: ClickHouseEventsDataConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | str:
    previous_sip_us: int | None = None
    if rows.shape[0] == span.expected_rows + 1 and int(rows["ordinal"][0]) == span.low_ordinal - 1:
        previous_sip_us = int(rows["sip_timestamp_us"][0])
        rows = rows[1:]
    if rows.shape[0] != span.expected_rows:
        return "row_count_mismatch"
    ordinals = rows["ordinal"]
    if ordinals[0] != span.low_ordinal or ordinals[-1] != span.high_ordinal:
        return "ordinal_range_mismatch"
    if np.any(ordinals[1:] != ordinals[:-1] + 1):
        return "ordinal_gap"
    headers = np.zeros((span.origins_per_span, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((span.origins_per_span, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    origin_ts = np.zeros((span.origins_per_span,), dtype=np.int64)
    for index in range(span.origins_per_span):
        origin_offset = config.events_per_chunk - 1 + index * span.stride
        start = origin_offset - config.events_per_chunk + 1
        end = origin_offset + 1
        if start > 0:
            window_previous_sip_us = int(rows["sip_timestamp_us"][start - 1])
        else:
            window_previous_sip_us = previous_sip_us
        encoded = encode_unified_event_window(rows[start:end], previous_sip_us=window_previous_sip_us)
        if isinstance(encoded, str):
            return encoded
        header, event_bytes = encoded
        headers[index] = header
        events[index] = event_bytes
        origin_ts[index] = int(rows["sip_timestamp_us"][origin_offset]) * 1000
    return headers, events, origin_ts


def encode_unified_event_window(rows: np.ndarray, *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray] | str:
    if rows.shape[0] != DEFAULT_CONTEXT_EVENTS:
        return "invalid_window_size"
    event_types = rows["event_type"].astype(np.uint8, copy=False)
    quote_positions = np.flatnonzero(event_types == QUOTE_EVENT_TYPE)
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

    sip_us = rows["sip_timestamp_us"].astype(np.int64, copy=False)
    deltas_us = np.zeros((rows.shape[0],), dtype=np.int64)
    deltas_us[1:] = np.maximum(0, sip_us[1:] - sip_us[:-1])
    quote_count = int(np.count_nonzero(event_types == QUOTE_EVENT_TYPE))
    trade_count = int(np.count_nonzero(event_types == TRADE_EVENT_TYPE))
    if quote_count > 255 or trade_count > 255:
        return "event_count_overflow"

    header = np.zeros((HEADER_BYTES,), dtype=np.uint8)
    put_uint_le(header, 0, ask_anchor_ticks, 3)
    header[2] &= 0x0F
    put_uint_le(header, 3, spread_anchor_ticks, 2)
    put_uint_le(header, 5, log_time_bucket_array(np.asarray([sip_us[-1] - sip_us[0]], dtype=np.int64))[0], 2)
    put_uint_le(header, 7, 0, 2)
    start_gap_us = 0 if previous_sip_us is None else max(0, int(sip_us[0]) - int(previous_sip_us))
    put_uint_le(header, 9, int(log_time_bucket_array(np.asarray([start_gap_us], dtype=np.int64))[0]), 2)
    header[11] = quote_count
    header[12] = trade_count
    header[13] = 0x01 | (0x02 if trade_count > 0 else 0) | (0x04 if tick_size == 0.01 else 0)

    events = np.zeros((DEFAULT_CONTEXT_EVENTS, EVENT_BYTES), dtype=np.uint8)
    events[:, 0] = ((event_types & 0x01) | 0x02).astype(np.uint8)
    write_uint16_columns(events[:, 1:3], log_time_bucket_array(deltas_us))

    quote_mask = event_types == QUOTE_EVENT_TYPE
    trade_mask = event_types == TRADE_EVENT_TYPE
    price_1 = np.zeros((DEFAULT_CONTEXT_EVENTS,), dtype=np.int64)
    price_2 = np.zeros((DEFAULT_CONTEXT_EVENTS,), dtype=np.int64)
    if np.any(quote_mask):
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
    write_int16_columns(events[:, 3:5], price_1)
    write_int16_columns(events[:, 5:7], price_2)
    size_primary = rows["size_primary"].astype(np.float64, copy=False)
    size_secondary = rows["size_secondary"].astype(np.float64, copy=False)
    events[:, 7] = size_bucket_array(size_primary)
    events[:, 8] = size_bucket_array(size_secondary)
    tape = (rows["event_flags"] >> 2) & 0x07
    events[:, 9] = (((size_primary > 0.0) & (size_primary < 100.0)).astype(np.uint8) | (((size_secondary > 0.0) & (size_secondary < 100.0)).astype(np.uint8) << 1) | ((tape & 0x07) << 2)).astype(np.uint8)
    events[:, 10] = rows["exchange_primary"] & 0x1F
    events[:, 11] = rows["exchange_secondary"] & 0x1F
    events[:, 12:16] = np.ascontiguousarray(rows["conditions_packed"].astype("<u4", copy=False)).view(np.uint8).reshape(-1, 4)
    return header, events


def decode_price_array(price_int: np.ndarray, scale: np.ndarray) -> np.ndarray:
    denominator = np.where(scale.astype(np.uint8, copy=False) == 1, 10000.0, 100.0)
    return price_int.astype(np.float64, copy=False) / denominator


def log_time_bucket_array(duration_us: np.ndarray, *, scale: int = 32, bits: int = 10) -> np.ndarray:
    values = np.rint(np.log2(1.0 + np.maximum(0, duration_us.astype(np.float64, copy=False))) * scale).astype(np.int64)
    return np.clip(values, 0, (1 << bits) - 1).astype(np.uint16)


def size_bucket_array(size: np.ndarray, *, scale: int = 16) -> np.ndarray:
    values = np.rint(np.log2(1.0 + np.maximum(0.0, size.astype(np.float64, copy=False)) / 100.0) * scale).astype(np.int64)
    return np.clip(values, 0, 255).astype(np.uint8)


def write_uint16_columns(target: np.ndarray, values: np.ndarray) -> None:
    target[:, :] = np.ascontiguousarray(values.astype("<u2", copy=False)).view(np.uint8).reshape(-1, 2)


def write_int16_columns(target: np.ndarray, values: np.ndarray) -> None:
    target[:, :] = np.ascontiguousarray(values.astype("<i2", copy=False)).view(np.uint8).reshape(-1, 2)


def put_uint_le(buffer: np.ndarray, offset: int, value: int, width: int) -> None:
    buffer[offset : offset + width] = np.frombuffer(int(value).to_bytes(width, byteorder="little", signed=False), dtype=np.uint8)
