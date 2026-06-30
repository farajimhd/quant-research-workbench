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

from research.mlops.clickhouse import (
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
DEFAULT_PAST_SPAN_EVENTS = 128
DEFAULT_FUTURE_SPAN_EVENTS = 0
DEFAULT_FUTURE_LABEL_HORIZONS = (128, 256, 512, 1024, 2048)
DEFAULT_ORIGINS_PER_SPAN = 32
DEFAULT_MIN_ORIGIN_STRIDE = 1
DEFAULT_MAX_ORIGIN_STRIDE = 16
DEFAULT_QUERY_BUNDLE_SPANS = 64

EVENT_ROW_DTYPE = np.dtype(
    [
        ("span_id", "<u4"),
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
    past_span_events: int = DEFAULT_PAST_SPAN_EVENTS
    future_span_events: int = DEFAULT_FUTURE_SPAN_EVENTS
    label_chunks: int = 0
    return_torch: bool = True


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
    url = (
        config.clickhouse_url
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or DEFAULT_CLICKHOUSE_URL
    )
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
    past_span_events = max(int(config.past_span_events), int(config.events_per_chunk))
    future_span_events = max(int(config.future_span_events), max(0, int(config.label_chunks)) * int(config.events_per_chunk))
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
        past_span_events=past_span_events,
        future_span_events=future_span_events,
        label_chunks=max(0, int(config.label_chunks)),
        return_torch=bool(config.return_torch),
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
    label_chunks = max(0, int(config.label_chunks))
    headers = np.zeros((config.batch_size, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((config.batch_size, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    label_headers = np.zeros((config.batch_size, label_chunks, HEADER_BYTES), dtype=np.uint8) if label_chunks else None
    label_events = (
        np.zeros((config.batch_size, label_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8) if label_chunks else None
    )
    label_columns: dict[str, list[np.ndarray | list[str]]] = {}
    origin_ts = np.zeros((config.batch_size,), dtype=np.int64)
    origin_ordinals = np.zeros((config.batch_size,), dtype=np.int64)
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
            if label_chunks:
                span_headers, span_events, span_label_headers, span_label_events, span_origin_ts, span_origin_ordinals, span_labels = encoded
            else:
                span_headers, span_events, span_origin_ts, span_origin_ordinals = encoded
            take = min(span_headers.shape[0], config.batch_size - filled)
            headers[filled : filled + take] = span_headers[:take]
            events[filled : filled + take] = span_events[:take]
            if label_chunks and label_headers is not None and label_events is not None:
                label_headers[filled : filled + take] = span_label_headers[:take]
                label_events[filled : filled + take] = span_label_events[:take]
                append_label_columns(label_columns, span_labels, take)
            origin_ts[filled : filled + take] = span_origin_ts[:take]
            origin_ordinals[filled : filled + take] = span_origin_ordinals[:take]
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
    batch: dict[str, object] = {
        "header_uint8": torch.from_numpy(headers) if config.return_torch else headers,
        "events_uint8": torch.from_numpy(events) if config.return_torch else events,
        "origin_timestamp_ns": torch.from_numpy(origin_ts) if config.return_torch else origin_ts,
        "origin_ordinal": torch.from_numpy(origin_ordinals) if config.return_torch else origin_ordinals,
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
    if label_chunks and label_headers is not None and label_events is not None:
        batch["label_header_uint8"] = torch.from_numpy(label_headers) if config.return_torch else label_headers
        batch["label_events_uint8"] = torch.from_numpy(label_events) if config.return_torch else label_events
        batch["label_chunks"] = label_chunks
        batch["labels"] = finalize_label_columns(label_columns)
    return batch


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
        past_span_events = max(int(config.past_span_events), int(config.events_per_chunk))
        min_base = row.first_ordinal + past_span_events - 1
        label_events = max(0, int(config.label_chunks)) * config.events_per_chunk
        future_span_events = max(int(config.future_span_events), label_events)
        max_base = row.max_valid_ordinal - high_extra - future_span_events
        if max_base < min_base:
            continue
        base = rng.randint(min_base, max_base)
        low = base - past_span_events + 1
        high = base + high_extra + future_span_events
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
    return np.frombuffer(payload, dtype=EVENT_ROW_DTYPE)


def span_query(config: ClickHouseEventsDataConfig, spans: list[EventSpan]) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    parts = [
        f"""
SELECT
    toUInt32({span.span_id}) AS span_id,
    ordinal,
    event_meta,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5
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
    event_meta,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | str:
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
    label_chunks = max(0, int(config.label_chunks))
    label_headers = np.zeros((span.origins_per_span, label_chunks, HEADER_BYTES), dtype=np.uint8) if label_chunks else None
    label_events = (
        np.zeros((span.origins_per_span, label_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8) if label_chunks else None
    )
    origin_ts = np.zeros((span.origins_per_span,), dtype=np.int64)
    origin_ordinals = np.zeros((span.origins_per_span,), dtype=np.int64)
    span_label_columns: dict[str, list[object]] = {}
    for index in range(span.origins_per_span):
        # The span may include more as-of-origin history than the 128-event x
        # chunk. Locate each origin from its absolute ordinal so the x chunk
        # remains the last 128 events ending at that origin.
        origin_offset = int(span.base_origin - span.low_ordinal) + index * span.stride
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
        if label_chunks and label_headers is not None and label_events is not None:
            for label_index in range(label_chunks):
                label_start = origin_offset + 1 + label_index * config.events_per_chunk
                label_end = label_start + config.events_per_chunk
                label_previous_sip_us = int(rows["sip_timestamp_us"][label_start - 1])
                encoded_label = encode_unified_event_window(rows[label_start:label_end], previous_sip_us=label_previous_sip_us)
                if isinstance(encoded_label, str):
                    return "label_" + encoded_label
                label_header, label_event_bytes = encoded_label
                label_headers[index, label_index] = label_header
                label_events[index, label_index] = label_event_bytes
            add_sample_label_row(
                span_label_columns,
                rows=rows,
                origin_offset=origin_offset,
                events_per_chunk=config.events_per_chunk,
                past_span_events=max(int(config.past_span_events), int(config.events_per_chunk)),
                future_span_events=max(int(config.future_span_events), label_chunks * int(config.events_per_chunk)),
                ticker=span.ticker,
            )
        origin_ts[index] = int(rows["sip_timestamp_us"][origin_offset]) * 1000
        origin_ordinals[index] = int(rows["ordinal"][origin_offset])
    if label_chunks and label_headers is not None and label_events is not None:
        return headers, events, label_headers, label_events, origin_ts, origin_ordinals, label_columns_to_arrays(span_label_columns)
    return headers, events, origin_ts, origin_ordinals


def add_sample_label_row(
    columns: dict[str, list[object]],
    *,
    rows: np.ndarray,
    origin_offset: int,
    events_per_chunk: int,
    past_span_events: int,
    future_span_events: int,
    ticker: str,
) -> None:
    origin_sip_us = int(rows["sip_timestamp_us"][origin_offset])
    origin_ordinal = int(rows["ordinal"][origin_offset])
    past_start = max(0, int(origin_offset) - max(1, int(past_span_events)) + 1)
    past_rows = rows[past_start : origin_offset + 1]
    event_types = (past_rows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    quote_positions = np.flatnonzero(event_types == QUOTE_EVENT_TYPE)
    trade_positions = np.flatnonzero(event_types == TRADE_EVENT_TYPE)
    append_label_value(columns, "ticker", ticker)
    append_label_value(columns, "origin_ordinal", origin_ordinal)
    append_label_value(columns, "origin_timestamp_us", origin_sip_us)
    append_label_value(columns, "past_2048_event_count", int(past_rows.shape[0]))
    append_label_value(columns, "past_2048_quote_count", int(quote_positions.size))
    append_label_value(columns, "past_2048_trade_count", int(trade_positions.size))
    append_label_value(columns, "past_2048_quote_count_ratio", float(quote_positions.size) / max(1.0, float(past_rows.shape[0])))
    append_label_value(columns, "past_2048_trade_count_ratio", float(trade_positions.size) / max(1.0, float(past_rows.shape[0])))
    append_label_value(columns, "past_2048_elapsed_us", max(0, origin_sip_us - int(past_rows["sip_timestamp_us"][0])))

    asof_quote = latest_event_in_current_chunk_with_previous_chunk_fallback(
        rows=rows,
        origin_offset=origin_offset,
        events_per_chunk=events_per_chunk,
        event_type=QUOTE_EVENT_TYPE,
    )
    asof_trade = latest_event_in_current_chunk_with_previous_chunk_fallback(
        rows=rows,
        origin_offset=origin_offset,
        events_per_chunk=events_per_chunk,
        event_type=TRADE_EVENT_TYPE,
    )
    add_quote_state(columns, "asof", asof_quote)
    add_trade_state(columns, "asof_last_trade", asof_trade)

    max_future_events = max(0, int(future_span_events))
    for horizon in DEFAULT_FUTURE_LABEL_HORIZONS:
        if horizon > max_future_events:
            continue
        future_rows = rows[origin_offset + 1 : origin_offset + 1 + horizon]
        add_future_horizon_labels(
            columns,
            rows=rows,
            future_rows=future_rows,
            horizon=horizon,
            origin_offset=origin_offset,
            events_per_chunk=events_per_chunk,
            origin_sip_us=origin_sip_us,
        )


def latest_event_in_current_chunk_with_previous_chunk_fallback(
    *,
    rows: np.ndarray,
    origin_offset: int,
    events_per_chunk: int,
    event_type: int,
) -> np.void | None:
    chunk_size = max(1, int(events_per_chunk))
    current_start = max(0, int(origin_offset) - chunk_size + 1)
    current_end = int(origin_offset) + 1
    return latest_event_in_window_with_previous_chunk_fallback(
        rows=rows,
        window_start=current_start,
        window_end=current_end,
        events_per_chunk=chunk_size,
        event_type=event_type,
    )


def latest_event_in_window_with_previous_chunk_fallback(
    *,
    rows: np.ndarray,
    window_start: int,
    window_end: int,
    events_per_chunk: int,
    event_type: int,
) -> np.void | None:
    chunk_size = max(1, int(events_per_chunk))
    current_start = max(0, int(window_start))
    current_end = min(int(rows.shape[0]), int(window_end))
    current_rows = rows[current_start:current_end]
    current_types = (current_rows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    current_positions = np.flatnonzero(current_types == int(event_type))
    if current_positions.size:
        return current_rows[int(current_positions[-1])]

    previous_start = max(0, current_start - chunk_size)
    previous_rows = rows[previous_start:current_start]
    if not previous_rows.shape[0]:
        return None
    previous_types = (previous_rows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    previous_positions = np.flatnonzero(previous_types == int(event_type))
    if previous_positions.size:
        return previous_rows[int(previous_positions[-1])]
    return None


def add_future_horizon_labels(
    columns: dict[str, list[object]],
    *,
    rows: np.ndarray,
    future_rows: np.ndarray,
    horizon: int,
    origin_offset: int,
    events_per_chunk: int,
    origin_sip_us: int,
) -> None:
    prefix = f"future_{horizon}"
    append_label_value(columns, f"{prefix}_elapsed_us", max(0, int(future_rows["sip_timestamp_us"][-1]) - origin_sip_us) if future_rows.shape[0] else 0)
    event_types = (future_rows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False) if future_rows.shape[0] else np.empty((0,), dtype=np.uint8)
    quote_positions = np.flatnonzero(event_types == QUOTE_EVENT_TYPE)
    trade_positions = np.flatnonzero(event_types == TRADE_EVENT_TYPE)
    append_label_value(columns, f"{prefix}_quote_count", int(quote_positions.size))
    append_label_value(columns, f"{prefix}_trade_count", int(trade_positions.size))

    horizon_end = int(origin_offset) + 1 + int(horizon)
    horizon_chunk_start = horizon_end - max(1, int(events_per_chunk))
    last_quote = latest_event_in_window_with_previous_chunk_fallback(
        rows=rows,
        window_start=horizon_chunk_start,
        window_end=horizon_end,
        events_per_chunk=events_per_chunk,
        event_type=QUOTE_EVENT_TYPE,
    )
    add_quote_state(columns, prefix, last_quote)

    if quote_positions.size or last_quote is not None:
        quote_rows = future_rows[quote_positions] if quote_positions.size else np.empty((0,), dtype=future_rows.dtype)
        high_price_int = int(last_quote["price_primary_int"]) if last_quote is not None else 0
        high_price_scale = int(condition_primary_price_scale(last_quote)) if last_quote is not None else 0
        high_elapsed_us = 0
        low_price_int = int(last_quote["price_secondary_int"]) if last_quote is not None else 0
        low_price_scale = int(condition_secondary_price_scale(last_quote)) if last_quote is not None else 0
        low_elapsed_us = 0
        ask_prices = quote_rows["price_primary_int"].astype(np.uint64, copy=False)
        bid_prices = quote_rows["price_secondary_int"].astype(np.uint64, copy=False)
        if ask_prices.size:
            high_pos = int(np.argmax(ask_prices))
            high_quote = quote_rows[high_pos]
            if last_quote is None or int(high_quote["price_primary_int"]) > high_price_int:
                high_price_int = int(high_quote["price_primary_int"])
                high_price_scale = int(condition_primary_price_scale(high_quote))
                high_elapsed_us = max(0, int(high_quote["sip_timestamp_us"]) - origin_sip_us)
        if bid_prices.size:
            low_pos = int(np.argmin(bid_prices))
            low_quote = quote_rows[low_pos]
            if last_quote is None or int(low_quote["price_secondary_int"]) < low_price_int:
                low_price_int = int(low_quote["price_secondary_int"])
                low_price_scale = int(condition_secondary_price_scale(low_quote))
                low_elapsed_us = max(0, int(low_quote["sip_timestamp_us"]) - origin_sip_us)
        add_price_label(
            columns,
            f"{prefix}_high_ask",
            price_int=high_price_int,
            scale=high_price_scale,
            elapsed_us=high_elapsed_us,
        )
        add_price_label(
            columns,
            f"{prefix}_low_bid",
            price_int=low_price_int,
            scale=low_price_scale,
            elapsed_us=low_elapsed_us,
        )
    else:
        add_price_label(columns, f"{prefix}_high_ask", price_int=0, scale=0, elapsed_us=0)
        add_price_label(columns, f"{prefix}_low_bid", price_int=0, scale=0, elapsed_us=0)

    if trade_positions.size:
        trade_rows = future_rows[trade_positions]
        trade_prices = trade_rows["price_primary_int"].astype(np.uint64, copy=False)
        max_pos = int(np.argmax(trade_prices))
        min_pos = int(np.argmin(trade_prices))
        max_trade = trade_rows[max_pos]
        min_trade = trade_rows[min_pos]
        add_price_label(
            columns,
            f"{prefix}_max_trade",
            price_int=int(max_trade["price_primary_int"]),
            scale=int(condition_primary_price_scale(max_trade)),
            elapsed_us=max(0, int(max_trade["sip_timestamp_us"]) - origin_sip_us),
        )
        add_price_label(
            columns,
            f"{prefix}_min_trade",
            price_int=int(min_trade["price_primary_int"]),
            scale=int(condition_primary_price_scale(min_trade)),
            elapsed_us=max(0, int(min_trade["sip_timestamp_us"]) - origin_sip_us),
        )
    else:
        add_price_label(columns, f"{prefix}_max_trade", price_int=0, scale=0, elapsed_us=0)
        add_price_label(columns, f"{prefix}_min_trade", price_int=0, scale=0, elapsed_us=0)


def add_quote_state(columns: dict[str, list[object]], prefix: str, quote: np.void | None) -> None:
    if quote is None:
        append_label_value(columns, f"{prefix}_has_quote", 0)
        append_label_value(columns, f"{prefix}_ask_price_int", 0)
        append_label_value(columns, f"{prefix}_ask_price_scale", 0)
        append_label_value(columns, f"{prefix}_ask_size", 0.0)
        append_label_value(columns, f"{prefix}_bid_price_int", 0)
        append_label_value(columns, f"{prefix}_bid_price_scale", 0)
        append_label_value(columns, f"{prefix}_bid_size", 0.0)
        return
    append_label_value(columns, f"{prefix}_has_quote", 1)
    append_label_value(columns, f"{prefix}_ask_price_int", int(quote["price_primary_int"]))
    append_label_value(columns, f"{prefix}_ask_price_scale", int(condition_primary_price_scale(quote)))
    append_label_value(columns, f"{prefix}_ask_size", float(quote["size_primary"]))
    append_label_value(columns, f"{prefix}_bid_price_int", int(quote["price_secondary_int"]))
    append_label_value(columns, f"{prefix}_bid_price_scale", int(condition_secondary_price_scale(quote)))
    append_label_value(columns, f"{prefix}_bid_size", float(quote["size_secondary"]))


def add_trade_state(columns: dict[str, list[object]], prefix: str, trade: np.void | None) -> None:
    if trade is None:
        append_label_value(columns, f"{prefix}_has_trade", 0)
        append_label_value(columns, f"{prefix}_price_int", 0)
        append_label_value(columns, f"{prefix}_price_scale", 0)
        append_label_value(columns, f"{prefix}_size", 0.0)
        return
    append_label_value(columns, f"{prefix}_has_trade", 1)
    append_label_value(columns, f"{prefix}_price_int", int(trade["price_primary_int"]))
    append_label_value(columns, f"{prefix}_price_scale", int(condition_primary_price_scale(trade)))
    append_label_value(columns, f"{prefix}_size", float(trade["size_primary"]))


def add_price_label(columns: dict[str, list[object]], prefix: str, *, price_int: int, scale: int, elapsed_us: int) -> None:
    append_label_value(columns, f"{prefix}_price_int", int(price_int))
    append_label_value(columns, f"{prefix}_price_scale", int(scale))
    append_label_value(columns, f"{prefix}_elapsed_us", int(elapsed_us))


def append_label_value(columns: dict[str, list[object]], key: str, value: object) -> None:
    columns.setdefault(key, []).append(value)


def label_columns_to_arrays(columns: dict[str, list[object]]) -> dict[str, np.ndarray | list[str]]:
    out: dict[str, np.ndarray | list[str]] = {}
    for key, values in columns.items():
        if key == "ticker":
            out[key] = [str(value) for value in values]
        elif key.endswith("_ratio") or key.endswith("_size"):
            out[key] = np.asarray(values, dtype=np.float32)
        elif key.endswith("_scale") or key.endswith("_has_quote") or key.endswith("_has_trade"):
            out[key] = np.asarray(values, dtype=np.uint8)
        elif key.endswith("_count"):
            out[key] = np.asarray(values, dtype=np.uint16)
        elif key.endswith("_price_int"):
            out[key] = np.asarray(values, dtype=np.uint32)
        else:
            out[key] = np.asarray(values, dtype=np.int64)
    return out


def append_label_columns(target: dict[str, list[np.ndarray | list[str]]], source: dict[str, np.ndarray | list[str]], take: int) -> None:
    for key, values in source.items():
        if isinstance(values, np.ndarray):
            target.setdefault(key, []).append(values[:take].copy())
        else:
            target.setdefault(key, []).append(list(values[:take]))


def finalize_label_columns(columns: dict[str, list[np.ndarray | list[str]]]) -> dict[str, np.ndarray | list[str]]:
    out: dict[str, np.ndarray | list[str]] = {}
    for key, chunks in columns.items():
        if not chunks:
            continue
        first = chunks[0]
        if isinstance(first, np.ndarray):
            out[key] = np.concatenate([chunk for chunk in chunks if isinstance(chunk, np.ndarray)])
        else:
            merged: list[str] = []
            for chunk in chunks:
                merged.extend(str(value) for value in chunk)
            out[key] = merged
    return out


def encode_unified_event_window(rows: np.ndarray, *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray] | str:
    if rows.shape[0] != DEFAULT_CONTEXT_EVENTS:
        return "invalid_window_size"
    event_types = (rows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    quote_positions = np.flatnonzero(event_types == QUOTE_EVENT_TYPE)
    if quote_positions.size == 0:
        return "no_quote_anchor"
    anchor_idx = int(quote_positions[-1])
    primary_prices = decode_price_array(rows["price_primary_int"], condition_primary_price_scale(rows))
    secondary_prices = decode_price_array(rows["price_secondary_int"], condition_secondary_price_scale(rows))
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
    tape = condition_tape_code(rows)
    events[:, 9] = (((size_primary > 0.0) & (size_primary < 100.0)).astype(np.uint8) | (((size_secondary > 0.0) & (size_secondary < 100.0)).astype(np.uint8) << 1) | ((tape & 0x07) << 2)).astype(np.uint8)
    events[:, 10] = rows["exchange_primary"] & 0x1F
    events[:, 11] = rows["exchange_secondary"] & 0x1F
    events[:, 12] = rows["condition_token_1"]
    events[:, 13] = rows["condition_token_2"]
    events[:, 14] = rows["condition_token_3"]
    events[:, 15] = rows["condition_token_4"]
    return header, events


def encode_unified_event_windows(
    windows: np.ndarray,
    *,
    previous_sip_us: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized equivalent of ``encode_unified_event_window``.

    Returns ``headers, events, valid, reasons``. Valid rows are byte-identical
    to the scalar encoder; invalid rows carry the same first rejection reason.
    """

    if windows.ndim != 2 or int(windows.shape[1]) != DEFAULT_CONTEXT_EVENTS:
        count = int(windows.shape[0]) if windows.ndim >= 1 else 0
        return (
            np.zeros((count, HEADER_BYTES), dtype=np.uint8),
            np.zeros((count, DEFAULT_CONTEXT_EVENTS, EVENT_BYTES), dtype=np.uint8),
            np.zeros((count,), dtype=np.bool_),
            np.asarray(["invalid_window_size"] * count, dtype=object),
        )
    count = int(windows.shape[0])
    headers = np.zeros((count, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((count, DEFAULT_CONTEXT_EVENTS, EVENT_BYTES), dtype=np.uint8)
    valid = np.ones((count,), dtype=np.bool_)
    reasons = np.empty((count,), dtype=object)
    reasons[:] = ""
    if count == 0:
        return headers, events, valid, reasons

    event_types = (windows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    quote_mask = event_types == QUOTE_EVENT_TYPE
    trade_mask = event_types == TRADE_EVENT_TYPE
    row_index = np.arange(count)

    has_quote = quote_mask.any(axis=1)
    _mark_invalid(valid, reasons, ~has_quote, "no_quote_anchor")
    reversed_quote = quote_mask[:, ::-1]
    anchor_idx = DEFAULT_CONTEXT_EVENTS - 1 - np.argmax(reversed_quote, axis=1)

    primary_prices = decode_price_array(windows["price_primary_int"], condition_primary_price_scale(windows))
    secondary_prices = decode_price_array(windows["price_secondary_int"], condition_secondary_price_scale(windows))
    anchor_ask = primary_prices[row_index, anchor_idx]
    anchor_bid = secondary_prices[row_index, anchor_idx]
    invalid_anchor = (anchor_ask <= 0.0) | (anchor_bid <= 0.0) | (anchor_ask < anchor_bid)
    _mark_invalid(valid, reasons, has_quote & invalid_anchor, "invalid_quote_anchor")

    tick_size = np.where(anchor_ask >= 1.0, 0.01, 0.0001)
    ask_anchor_ticks = np.rint(anchor_ask / tick_size).astype(np.int64)
    spread_anchor_ticks = np.rint((anchor_ask - anchor_bid) / tick_size).astype(np.int64)
    _mark_invalid(valid, reasons, ask_anchor_ticks >= 2**20, "ask_anchor_overflow")
    _mark_invalid(valid, reasons, spread_anchor_ticks >= 2**16, "spread_anchor_overflow")

    sip_us = windows["sip_timestamp_us"].astype(np.int64, copy=False)
    deltas_us = np.zeros((count, DEFAULT_CONTEXT_EVENTS), dtype=np.int64)
    deltas_us[:, 1:] = np.maximum(0, sip_us[:, 1:] - sip_us[:, :-1])
    quote_count = np.count_nonzero(quote_mask, axis=1)
    trade_count = np.count_nonzero(trade_mask, axis=1)
    _mark_invalid(valid, reasons, (quote_count > 255) | (trade_count > 255), "event_count_overflow")

    ask_valid = (primary_prices > 0.0) & (secondary_prices > 0.0) & (primary_prices >= secondary_prices)
    invalid_quote_event = np.any(quote_mask & ~ask_valid, axis=1)
    _mark_invalid(valid, reasons, invalid_quote_event, "invalid_quote_event")
    invalid_trade_event = np.any(trade_mask & (primary_prices <= 0.0), axis=1)
    _mark_invalid(valid, reasons, invalid_trade_event, "invalid_trade_event")

    price_1 = np.zeros((count, DEFAULT_CONTEXT_EVENTS), dtype=np.int64)
    price_2 = np.zeros((count, DEFAULT_CONTEXT_EVENTS), dtype=np.int64)
    ask_ticks = np.rint(primary_prices / tick_size[:, None]).astype(np.int64)
    spread_ticks = np.rint((primary_prices - secondary_prices) / tick_size[:, None]).astype(np.int64)
    trade_ticks = ask_ticks
    price_1[quote_mask] = (ask_ticks - ask_anchor_ticks[:, None])[quote_mask]
    price_2[quote_mask] = (spread_ticks - spread_anchor_ticks[:, None])[quote_mask]
    price_1[trade_mask] = (trade_ticks - ask_anchor_ticks[:, None])[trade_mask]
    price_overflow = np.any((price_1 < -32768) | (price_1 > 32767) | (price_2 < -32768) | (price_2 > 32767), axis=1)
    _mark_invalid(valid, reasons, price_overflow, "price_delta_overflow")

    _put_uint_le_columns(headers, 0, ask_anchor_ticks, 3)
    headers[:, 2] &= 0x0F
    _put_uint_le_columns(headers, 3, spread_anchor_ticks, 2)
    _put_uint_le_columns(headers, 5, log_time_bucket_array(sip_us[:, -1] - sip_us[:, 0]), 2)
    _put_uint_le_columns(headers, 7, np.zeros((count,), dtype=np.int64), 2)
    if previous_sip_us is None:
        start_gap_us = np.zeros((count,), dtype=np.int64)
    else:
        previous = np.asarray(previous_sip_us, dtype=np.int64)
        start_gap_us = np.where(previous < 0, 0, np.maximum(0, sip_us[:, 0] - previous))
    _put_uint_le_columns(headers, 9, log_time_bucket_array(start_gap_us).astype(np.int64), 2)
    headers[:, 11] = quote_count.astype(np.uint8)
    headers[:, 12] = trade_count.astype(np.uint8)
    headers[:, 13] = (
        0x01
        | ((trade_count > 0).astype(np.uint8) << 1)
        | ((tick_size == 0.01).astype(np.uint8) << 2)
    ).astype(np.uint8)

    events[:, :, 0] = ((event_types & 0x01) | 0x02).astype(np.uint8)
    events[:, :, 1:3] = np.ascontiguousarray(log_time_bucket_array(deltas_us).astype("<u2", copy=False)).view(np.uint8).reshape(
        count, DEFAULT_CONTEXT_EVENTS, 2
    )
    events[:, :, 3:5] = np.ascontiguousarray(price_1.astype("<i2", copy=False)).view(np.uint8).reshape(count, DEFAULT_CONTEXT_EVENTS, 2)
    events[:, :, 5:7] = np.ascontiguousarray(price_2.astype("<i2", copy=False)).view(np.uint8).reshape(count, DEFAULT_CONTEXT_EVENTS, 2)
    size_primary = windows["size_primary"].astype(np.float64, copy=False)
    size_secondary = windows["size_secondary"].astype(np.float64, copy=False)
    events[:, :, 7] = size_bucket_array(size_primary)
    events[:, :, 8] = size_bucket_array(size_secondary)
    tape = condition_tape_code(windows)
    events[:, :, 9] = (
        ((size_primary > 0.0) & (size_primary < 100.0)).astype(np.uint8)
        | (((size_secondary > 0.0) & (size_secondary < 100.0)).astype(np.uint8) << 1)
        | ((tape & 0x07) << 2)
    ).astype(np.uint8)
    events[:, :, 10] = windows["exchange_primary"] & 0x1F
    events[:, :, 11] = windows["exchange_secondary"] & 0x1F
    events[:, :, 12] = windows["condition_token_1"]
    events[:, :, 13] = windows["condition_token_2"]
    events[:, :, 14] = windows["condition_token_3"]
    events[:, :, 15] = windows["condition_token_4"]
    reasons[valid] = ""
    return headers, events, valid, reasons


def validate_unified_event_windows(windows: np.ndarray) -> np.ndarray:
    """Return scalar-compatible validity flags without packing output bytes."""

    if windows.ndim != 2 or int(windows.shape[1]) != DEFAULT_CONTEXT_EVENTS:
        count = int(windows.shape[0]) if windows.ndim >= 1 else 0
        return np.zeros((count,), dtype=np.bool_)
    count = int(windows.shape[0])
    valid = np.ones((count,), dtype=np.bool_)
    if count == 0:
        return valid

    event_types = (windows["event_meta"].astype(np.uint8, copy=False) & 0x01).astype(np.uint8, copy=False)
    quote_mask = event_types == QUOTE_EVENT_TYPE
    trade_mask = event_types == TRADE_EVENT_TYPE
    row_index = np.arange(count)

    has_quote = quote_mask.any(axis=1)
    valid &= has_quote
    reversed_quote = quote_mask[:, ::-1]
    anchor_idx = DEFAULT_CONTEXT_EVENTS - 1 - np.argmax(reversed_quote, axis=1)

    primary_prices = decode_price_array(windows["price_primary_int"], condition_primary_price_scale(windows))
    secondary_prices = decode_price_array(windows["price_secondary_int"], condition_secondary_price_scale(windows))
    anchor_ask = primary_prices[row_index, anchor_idx]
    anchor_bid = secondary_prices[row_index, anchor_idx]
    valid &= ~((anchor_ask <= 0.0) | (anchor_bid <= 0.0) | (anchor_ask < anchor_bid))

    tick_size = np.where(anchor_ask >= 1.0, 0.01, 0.0001)
    ask_anchor_ticks = np.rint(anchor_ask / tick_size).astype(np.int64)
    spread_anchor_ticks = np.rint((anchor_ask - anchor_bid) / tick_size).astype(np.int64)
    valid &= ask_anchor_ticks < 2**20
    valid &= spread_anchor_ticks < 2**16

    quote_count = np.count_nonzero(quote_mask, axis=1)
    trade_count = np.count_nonzero(trade_mask, axis=1)
    valid &= (quote_count <= 255) & (trade_count <= 255)

    ask_valid = (primary_prices > 0.0) & (secondary_prices > 0.0) & (primary_prices >= secondary_prices)
    valid &= ~np.any(quote_mask & ~ask_valid, axis=1)
    valid &= ~np.any(trade_mask & (primary_prices <= 0.0), axis=1)

    price_1 = np.zeros((count, DEFAULT_CONTEXT_EVENTS), dtype=np.int64)
    price_2 = np.zeros((count, DEFAULT_CONTEXT_EVENTS), dtype=np.int64)
    ask_ticks = np.rint(primary_prices / tick_size[:, None]).astype(np.int64)
    spread_ticks = np.rint((primary_prices - secondary_prices) / tick_size[:, None]).astype(np.int64)
    price_1[quote_mask] = (ask_ticks - ask_anchor_ticks[:, None])[quote_mask]
    price_2[quote_mask] = (spread_ticks - spread_anchor_ticks[:, None])[quote_mask]
    price_1[trade_mask] = (ask_ticks - ask_anchor_ticks[:, None])[trade_mask]
    valid &= ~np.any((price_1 < -32768) | (price_1 > 32767) | (price_2 < -32768) | (price_2 > 32767), axis=1)
    return valid


def _mark_invalid(valid: np.ndarray, reasons: np.ndarray, mask: np.ndarray, reason: str) -> None:
    target = np.asarray(mask, dtype=np.bool_) & valid
    if np.any(target):
        reasons[target] = reason
        valid[target] = False


def _put_uint_le_columns(buffer: np.ndarray, offset: int, values: np.ndarray, width: int) -> None:
    unsigned = np.asarray(values, dtype=np.int64) & ((1 << (8 * int(width))) - 1)
    for byte_index in range(int(width)):
        buffer[:, int(offset) + byte_index] = ((unsigned >> (8 * byte_index)) & 0xFF).astype(np.uint8)


def condition_primary_price_scale(rows: np.ndarray | np.void) -> np.ndarray | np.integer:
    return (rows["event_meta"] >> 1) & 1


def condition_secondary_price_scale(rows: np.ndarray | np.void) -> np.ndarray | np.integer:
    return (rows["event_meta"] >> 2) & 1


def condition_tape_code(rows: np.ndarray | np.void) -> np.ndarray | np.integer:
    return (rows["event_meta"] >> 3) & 0x07


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
