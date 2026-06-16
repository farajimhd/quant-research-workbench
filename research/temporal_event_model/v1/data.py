from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import numpy as np
import torch
from torch.utils.data import IterableDataset, get_worker_info

from research.mlops.clickhouse_events import (
    EVENT_ROW_DTYPE,
    PersistentClickHouseBytesClient,
    encode_unified_event_window,
    query_settings,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES
from research.temporal_event_model.v1.config import DataConfig


MICROSECONDS_PER_DAY = 86_400_000_000


@dataclass(frozen=True, slots=True)
class TemporalIndexRow:
    ticker: str
    first_ordinal: int
    last_ordinal: int
    first_sip_timestamp_us: int
    last_sip_timestamp_us: int
    split_event_count: int


@dataclass(frozen=True, slots=True)
class TemporalValidationBlock:
    ticker: str
    start_us: int
    end_us: int
    stride: int


class TemporalClickHouseBlockDataset(IterableDataset):
    """Yield training batches from random ticker/15-day windows.

    One ClickHouse query loads a single ticker block. The CPU then rolls all
    valid origins for the chosen stride and shuffles those origins before
    yielding batches. This amortizes query cost and keeps the GPU-facing batch
    tensors independent of ClickHouse latency once a block is loaded.
    """

    def __init__(self, config: DataConfig, *, split: str, batch_size: int, seed: int) -> None:
        super().__init__()
        self.config = normalized_data_config(config)
        self.split = split
        self.batch_size = int(batch_size)
        self.seed = int(seed)

    def __iter__(self) -> Iterator[dict[str, object]]:
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.seed + worker_id * 1009)
        text_client = ClickHouseHttpClient(self.config.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
        bytes_client = PersistentClickHouseBytesClient(self.config.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
        try:
            index_rows = load_temporal_index_rows(text_client, self.config, split=self.split)
            while True:
                block = load_random_temporal_block(bytes_client, index_rows, self.config, rng)
                if block is None:
                    continue
                for batch in iter_block_batches(block, self.config, self.batch_size, rng):
                    yield batch
        finally:
            bytes_client.close()


def normalized_data_config(config: DataConfig) -> DataConfig:
    url = (
        config.clickhouse_url
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or "http://localhost:18123"
    )
    if config.events_per_chunk != 128:
        raise ValueError("v1 temporal loader currently expects events_per_chunk=128.")
    if config.target_chunks != 1:
        raise ValueError("v1 temporal loader currently supports target_chunks=1.")
    if config.context_chunks < 1:
        raise ValueError("context_chunks must be positive.")
    if config.context_lag_schedule not in {"dense_geometric", "consecutive"}:
        raise ValueError("context_lag_schedule must be dense_geometric or consecutive.")
    if config.context_dense_fraction <= 0.0 or config.context_dense_fraction > 1.0:
        raise ValueError("context_dense_fraction must be in (0, 1].")
    if not config.train_stride_choices:
        raise ValueError("At least one training stride is required.")
    if not config.validation_stride_choices:
        raise ValueError("At least one validation stride is required.")
    return DataConfig(
        clickhouse_url=url,
        clickhouse_database=config.clickhouse_database,
        events_table=config.events_table,
        train_index_table=config.train_index_table,
        validation_index_table=config.validation_index_table,
        tickers=config.tickers,
        events_per_chunk=config.events_per_chunk,
        context_chunks=config.context_chunks,
        target_chunks=config.target_chunks,
        window_days=config.window_days,
        context_lag_schedule=config.context_lag_schedule,
        context_dense_fraction=float(config.context_dense_fraction),
        context_max_lag_steps=max(config.context_chunks - 1, int(config.context_max_lag_steps)),
        train_stride_choices=tuple(int(value) for value in config.train_stride_choices),
        validation_stride_choices=tuple(int(value) for value in config.validation_stride_choices),
        origin_stride_events=max(1, int(config.origin_stride_events)),
        block_max_events=int(config.block_max_events),
        min_samples_per_block=int(config.min_samples_per_block),
        validation_blocks=int(config.validation_blocks),
        validation_batches_per_block=int(config.validation_batches_per_block),
        clickhouse_max_threads=int(config.clickhouse_max_threads),
        clickhouse_max_memory_usage=str(config.clickhouse_max_memory_usage),
    )


def load_temporal_index_rows(client: ClickHouseHttpClient, config: DataConfig, *, split: str) -> list[TemporalIndexRow]:
    table = config.validation_index_table if split == "validation" else config.train_index_table
    ticker_filter = ""
    tickers = tuple(value.upper() for value in config.tickers if value)
    if tickers and tickers != ("ALL",) and "*" not in tickers:
        ticker_filter = "AND ticker IN (" + ", ".join(sql_string(value) for value in tickers) + ")"
    required_events = required_event_lookback(config, max(config.validation_stride_choices if split == "validation" else config.train_stride_choices))
    query = f"""
SELECT
    ticker,
    first_ordinal,
    last_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    split_event_count
FROM {quote_ident(config.clickhouse_database)}.{quote_ident(table)}
WHERE split_event_count >= {int(required_events)}
  AND last_sip_timestamp_us > first_sip_timestamp_us
  {ticker_filter}
ORDER BY ticker
FORMAT TSV
"""
    rows: list[TemporalIndexRow] = []
    for line in client.execute(query).splitlines():
        if not line:
            continue
        ticker, first_ord, last_ord, first_ts, last_ts, count = line.split("\t")
        rows.append(
            TemporalIndexRow(
                ticker=ticker,
                first_ordinal=int(first_ord),
                last_ordinal=int(last_ord),
                first_sip_timestamp_us=int(first_ts),
                last_sip_timestamp_us=int(last_ts),
                split_event_count=int(count),
            )
        )
    if not rows:
        raise RuntimeError(f"No eligible temporal rows found in {config.clickhouse_database}.{table}.")
    return rows


def required_event_lookback(config: DataConfig, stride: int) -> int:
    max_lag = max(context_lag_steps(config))
    return config.events_per_chunk + max_lag * stride + config.events_per_chunk


def load_random_temporal_block(
    client: PersistentClickHouseBytesClient,
    index_rows: list[TemporalIndexRow],
    config: DataConfig,
    rng: random.Random,
) -> dict[str, object] | None:
    started = time.perf_counter()
    for _ in range(100):
        row = rng.choice(index_rows)
        window_us = int(config.window_days * MICROSECONDS_PER_DAY)
        if row.last_sip_timestamp_us - row.first_sip_timestamp_us <= window_us:
            start_us = row.first_sip_timestamp_us
        else:
            start_us = rng.randint(row.first_sip_timestamp_us, row.last_sip_timestamp_us - window_us)
        end_us = start_us + window_us
        stride = rng.choice(config.train_stride_choices)
        query_started = time.perf_counter()
        raw_rows = fetch_ticker_time_window(client, config, row.ticker, start_us, end_us)
        query_seconds = time.perf_counter() - query_started
        if raw_rows.shape[0] > config.block_max_events:
            raw_rows = crop_random_event_subrange(raw_rows, config, stride, rng)
        valid_origins = valid_origin_offsets(raw_rows.shape[0], config, stride)
        if valid_origins.size < config.min_samples_per_block:
            continue
        shuffle_numpy_in_place(valid_origins, rng)
        return {
            "ticker": row.ticker,
            "start_us": start_us,
            "end_us": end_us,
            "stride": stride,
            "rows": raw_rows,
            "origins": valid_origins,
            "profile": {
                "data/block_load_seconds": time.perf_counter() - started,
                "data/block_query_seconds": query_seconds,
                "data/block_rows": float(raw_rows.shape[0]),
                "data/block_valid_origins": float(valid_origins.size),
                "data/context_stride_events": float(stride),
            },
        }
    return None


def build_fixed_validation_blocks(
    config: DataConfig,
    *,
    seed: int,
) -> list[TemporalValidationBlock]:
    normalized = normalized_data_config(config)
    rng = random.Random(seed)
    text_client = ClickHouseHttpClient(normalized.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
    rows = load_temporal_index_rows(text_client, normalized, split="validation")
    blocks: list[TemporalValidationBlock] = []
    window_us = int(normalized.window_days * MICROSECONDS_PER_DAY)
    attempts = 0
    while len(blocks) < normalized.validation_blocks and attempts < normalized.validation_blocks * 100:
        attempts += 1
        row = rng.choice(rows)
        if row.last_sip_timestamp_us - row.first_sip_timestamp_us <= window_us:
            start_us = row.first_sip_timestamp_us
        else:
            start_us = rng.randint(row.first_sip_timestamp_us, row.last_sip_timestamp_us - window_us)
        blocks.append(
            TemporalValidationBlock(
                ticker=row.ticker,
                start_us=start_us,
                end_us=start_us + window_us,
                stride=rng.choice(normalized.validation_stride_choices),
            )
        )
    if not blocks:
        raise RuntimeError("Could not build any fixed temporal validation blocks.")
    return blocks


def iter_fixed_validation_batches(
    config: DataConfig,
    blocks: list[TemporalValidationBlock],
    *,
    batch_size: int,
    seed: int,
) -> Iterator[dict[str, object]]:
    normalized = normalized_data_config(config)
    rng = random.Random(seed)
    client = PersistentClickHouseBytesClient(normalized.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
    try:
        for block in blocks:
            rows = fetch_ticker_time_window(client, normalized, block.ticker, block.start_us, block.end_us)
            if rows.shape[0] > normalized.block_max_events:
                rows = crop_random_event_subrange(rows, normalized, block.stride, rng)
            origins = valid_origin_offsets(rows.shape[0], normalized, block.stride)
            if origins.size == 0:
                continue
            shuffle_numpy_in_place(origins, rng)
            materialized = {"ticker": block.ticker, "stride": block.stride, "rows": rows, "origins": origins, "profile": {}}
            for idx, batch in enumerate(iter_block_batches(materialized, normalized, batch_size, rng)):
                if idx >= normalized.validation_batches_per_block:
                    break
                yield batch
    finally:
        client.close()


def fetch_ticker_time_window(client: PersistentClickHouseBytesClient, config: DataConfig, ticker: str, start_us: int, end_us: int) -> np.ndarray:
    table = f"{quote_ident(config.clickhouse_database)}.{quote_ident(config.events_table)}"
    query = f"""
SELECT
    toUInt32(0) AS span_id,
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
PREWHERE ticker = {sql_string(ticker)}
WHERE sip_timestamp_us >= {int(start_us)}
  AND sip_timestamp_us < {int(end_us)}
ORDER BY ordinal
{query_settings_proxy(config)}
FORMAT RowBinary
"""
    payload = client.execute_bytes(query)
    if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
        raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
    return np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()


def query_settings_proxy(config: DataConfig) -> str:
    class _Proxy:
        max_threads = config.clickhouse_max_threads
        max_memory_usage = config.clickhouse_max_memory_usage

    return query_settings(_Proxy())


def crop_random_event_subrange(rows: np.ndarray, config: DataConfig, stride: int, rng: random.Random) -> np.ndarray:
    required = required_event_lookback(config, stride)
    keep = max(required + config.min_samples_per_block, min(config.block_max_events, rows.shape[0]))
    if keep >= rows.shape[0]:
        return rows
    start = rng.randint(0, rows.shape[0] - keep)
    return rows[start : start + keep].copy()


def valid_origin_offsets(row_count: int, config: DataConfig, stride: int) -> np.ndarray:
    oldest_start = max(context_lag_steps(config)) * stride + config.events_per_chunk - 1
    latest_origin = row_count - config.events_per_chunk - 1
    if latest_origin < oldest_start:
        return np.empty((0,), dtype=np.int64)
    return np.arange(oldest_start, latest_origin + 1, max(1, config.origin_stride_events), dtype=np.int64)


def iter_block_batches(block: dict[str, object], config: DataConfig, batch_size: int, rng: random.Random) -> Iterator[dict[str, object]]:
    rows = block["rows"]
    origins = block["origins"]
    if not isinstance(rows, np.ndarray) or not isinstance(origins, np.ndarray):
        raise TypeError("Invalid temporal block payload.")
    stride = int(block["stride"])
    ticker = str(block["ticker"])
    offset = 0
    while offset < origins.size:
        batch, offset = materialize_next_temporal_batch(rows, origins, offset, config, stride, ticker, block.get("profile", {}), batch_size)
        if batch is None:
            break
        yield batch


def materialize_next_temporal_batch(
    rows: np.ndarray,
    origins: np.ndarray,
    offset: int,
    config: DataConfig,
    stride: int,
    ticker: str,
    block_profile: object,
    batch_size: int,
) -> tuple[dict[str, object] | None, int]:
    started = time.perf_counter()
    context_headers = np.zeros((batch_size, config.context_chunks, HEADER_BYTES), dtype=np.uint8)
    context_events = np.zeros((batch_size, config.context_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    target_headers = np.zeros((batch_size, config.target_chunks, HEADER_BYTES), dtype=np.uint8)
    target_events = np.zeros((batch_size, config.target_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    origin_ts = np.zeros((batch_size,), dtype=np.int64)
    origin_ordinals = np.zeros((batch_size,), dtype=np.int64)
    filled = 0
    cursor = int(offset)
    rejected = 0
    while cursor < origins.size and filled < batch_size:
        origin = int(origins[cursor])
        cursor += 1
        encoded = encode_one_temporal_sample(rows, origin, config, stride)
        if encoded is None:
            rejected += 1
            continue
        sample_context_headers, sample_context_events, sample_target_header, sample_target_events = encoded
        context_headers[filled] = sample_context_headers
        context_events[filled] = sample_context_events
        target_headers[filled, 0] = sample_target_header
        target_events[filled, 0] = sample_target_events
        origin_ts[filled] = int(rows["sip_timestamp_us"][origin]) * 1000
        origin_ordinals[filled] = int(rows["ordinal"][origin])
        filled += 1
    if filled < batch_size:
        return None, cursor
    profile = dict(block_profile) if isinstance(block_profile, dict) else {}
    profile.update(
        {
            "data/batch_materialize_seconds": time.perf_counter() - started,
            "data/context_stride_events": float(stride),
            "data/context_chunks": float(config.context_chunks),
            "data/context_max_lag_steps": float(max(context_lag_steps(config))),
            "data/context_max_lag_events": float(max(context_lag_steps(config)) * stride),
            "data/batch_samples": float(batch_size),
            "data/rejected_origins_in_materializer": float(rejected),
        }
    )
    return (
        {
            "context_header_uint8": torch.from_numpy(context_headers),
            "context_events_uint8": torch.from_numpy(context_events),
            "target_header_uint8": torch.from_numpy(target_headers),
            "target_events_uint8": torch.from_numpy(target_events),
            "origin_timestamp_ns": torch.from_numpy(origin_ts),
            "origin_ordinal": torch.from_numpy(origin_ordinals),
            "ticker": [ticker] * batch_size,
            "profile": profile,
        },
        cursor,
    )


def materialize_temporal_batch(
    rows: np.ndarray,
    origins: np.ndarray,
    config: DataConfig,
    stride: int,
    ticker: str,
    block_profile: object,
) -> dict[str, object]:
    started = time.perf_counter()
    batch_size = int(origins.shape[0])
    context_headers = np.zeros((batch_size, config.context_chunks, HEADER_BYTES), dtype=np.uint8)
    context_events = np.zeros((batch_size, config.context_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    target_headers = np.zeros((batch_size, config.target_chunks, HEADER_BYTES), dtype=np.uint8)
    target_events = np.zeros((batch_size, config.target_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    origin_ts = np.zeros((batch_size,), dtype=np.int64)
    origin_ordinals = np.zeros((batch_size,), dtype=np.int64)
    for sample_idx, origin in enumerate(origins):
        context_ends = context_end_offsets(int(origin), config, stride)
        for chunk_idx, end in enumerate(context_ends):
            header, events = encode_window_or_raise(rows, end - config.events_per_chunk + 1, end + 1)
            context_headers[sample_idx, chunk_idx] = header
            context_events[sample_idx, chunk_idx] = events
        target_start = int(origin) + 1
        target_end = target_start + config.events_per_chunk
        header, events = encode_window_or_raise(rows, target_start, target_end)
        target_headers[sample_idx, 0] = header
        target_events[sample_idx, 0] = events
        origin_ts[sample_idx] = int(rows["sip_timestamp_us"][origin]) * 1000
        origin_ordinals[sample_idx] = int(rows["ordinal"][origin])
    profile = dict(block_profile) if isinstance(block_profile, dict) else {}
    profile.update(
        {
            "data/batch_materialize_seconds": time.perf_counter() - started,
            "data/context_stride_events": float(stride),
            "data/context_chunks": float(config.context_chunks),
            "data/context_max_lag_steps": float(max(context_lag_steps(config))),
            "data/context_max_lag_events": float(max(context_lag_steps(config)) * stride),
            "data/batch_samples": float(batch_size),
        }
    )
    return {
        "context_header_uint8": torch.from_numpy(context_headers),
        "context_events_uint8": torch.from_numpy(context_events),
        "target_header_uint8": torch.from_numpy(target_headers),
        "target_events_uint8": torch.from_numpy(target_events),
        "origin_timestamp_ns": torch.from_numpy(origin_ts),
        "origin_ordinal": torch.from_numpy(origin_ordinals),
        "ticker": [ticker] * batch_size,
        "profile": profile,
    }


def encode_one_temporal_sample(
    rows: np.ndarray,
    origin: int,
    config: DataConfig,
    stride: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    context_headers = np.zeros((config.context_chunks, HEADER_BYTES), dtype=np.uint8)
    context_events = np.zeros((config.context_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    try:
        context_ends = context_end_offsets(int(origin), config, stride)
        for chunk_idx, end in enumerate(context_ends):
            encoded = encode_window_or_raise(rows, end - config.events_per_chunk + 1, end + 1)
            context_headers[chunk_idx] = encoded[0]
            context_events[chunk_idx] = encoded[1]
        target_start = int(origin) + 1
        target_header, target_events = encode_window_or_raise(rows, target_start, target_start + config.events_per_chunk)
        return context_headers, context_events, target_header, target_events
    except Exception:
        return None


def context_end_offsets(origin: int, config: DataConfig, stride: int) -> list[int]:
    """Return chunk end offsets in temporal order from oldest to newest.

    Lags are expressed in embedding-stride units. With `stride=1`, this exactly
    matches the rolling sequence `e1=[1..N]`, `e2=[2..N+1]`, and so on. With a
    larger stride, the same logical lag schedule is sampled more sparsely:
    `end = origin - lag_step * stride`.
    """

    return [int(origin) - lag_step * int(stride) for lag_step in reversed(context_lag_steps(config))]


def context_lag_steps(config: DataConfig) -> tuple[int, ...]:
    """Build dense-short / geometric-long context lags.

    For `context_chunks=64`, the default schedule uses 32 dense recent lags
    (`0..31`) and 32 geometric lags out to `context_max_lag_steps`. The temporal
    model receives them oldest-to-newest after `context_end_offsets` reverses
    this ascending tuple.
    """

    n = int(config.context_chunks)
    if n <= 0:
        raise ValueError("context_chunks must be positive.")
    if str(config.context_lag_schedule) == "consecutive":
        return tuple(range(n))

    max_lag = max(n - 1, int(config.context_max_lag_steps))
    dense_count = max(1, min(n, int(round(n * float(config.context_dense_fraction)))))
    dense = list(range(dense_count))
    tail_count = n - dense_count
    if tail_count <= 0:
        return tuple(dense[:n])

    start = dense_count
    selected: set[int] = set(dense)
    if tail_count == 1:
        selected.add(max_lag)
    else:
        ratio = (max_lag / max(1, start)) ** (1.0 / max(1, tail_count - 1))
        for idx in range(tail_count):
            raw = start * (ratio**idx)
            selected.add(max(start, min(max_lag, int(round(raw)))))

    if len(selected) < n:
        fill_missing_lags(selected, start=start, max_lag=max_lag, target_count=n)
    if len(selected) > n:
        dense_set = set(dense)
        tail = sorted(value for value in selected if value not in dense_set)
        tail = choose_evenly_spaced(tail, n - dense_count)
        selected = set(dense) | set(tail)
    return tuple(sorted(selected))


def fill_missing_lags(selected: set[int], *, start: int, max_lag: int, target_count: int) -> None:
    candidates = [value for value in range(start, max_lag + 1) if value not in selected]
    if not candidates:
        return
    need = target_count - len(selected)
    for value in choose_evenly_spaced(candidates, need):
        selected.add(value)


def choose_evenly_spaced(values: list[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if count >= len(values):
        return list(values)
    if count == 1:
        return [values[-1]]
    positions = np.linspace(0, len(values) - 1, count)
    chosen: list[int] = []
    used: set[int] = set()
    for position in positions:
        index = int(round(float(position)))
        while index in used and index + 1 < len(values):
            index += 1
        while index in used and index > 0:
            index -= 1
        used.add(index)
        chosen.append(values[index])
    return sorted(chosen)


def encode_window_or_raise(rows: np.ndarray, start: int, end: int) -> tuple[np.ndarray, np.ndarray]:
    previous_sip_us = int(rows["sip_timestamp_us"][start - 1]) if start > 0 else None
    encoded = encode_unified_event_window(rows[start:end], previous_sip_us=previous_sip_us)
    if isinstance(encoded, str):
        raise RuntimeError(f"Failed to encode temporal window {start}:{end}: {encoded}")
    return encoded


def shuffle_numpy_in_place(values: np.ndarray, rng: random.Random) -> None:
    for index in range(values.shape[0] - 1, 0, -1):
        swap = rng.randint(0, index)
        if swap != index:
            values[index], values[swap] = values[swap], values[index]


def timestamp_us_to_iso(timestamp_us: int) -> str:
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc).isoformat()
