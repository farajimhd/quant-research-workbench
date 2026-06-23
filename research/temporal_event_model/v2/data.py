from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import torch

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_user, quote_ident, sql_string
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient, decode_price_array, encode_unified_event_window, query_settings
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES, QUOTE_EVENT_TYPE
from research.temporal_event_model.v2.config import DataConfig


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


@dataclass(slots=True)
class ReturnTemporalBlock:
    ticker: str
    stride: int
    rows: np.ndarray
    origins: np.ndarray
    asof_mid_price: np.ndarray
    profile: dict[str, float]


def normalized_data_config(config: DataConfig) -> DataConfig:
    url = (
        config.clickhouse_url
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or "http://localhost:18123"
    )
    if config.events_per_chunk != 128:
        raise ValueError("v2 temporal loader expects events_per_chunk=128.")
    if config.context_chunks < 1:
        raise ValueError("context_chunks must be positive.")
    if not config.future_event_horizons:
        raise ValueError("At least one future event horizon is required.")
    if min(config.future_event_horizons) <= 0:
        raise ValueError("future_event_horizons must be positive.")
    if config.context_lag_schedule not in {"dense_geometric", "consecutive"}:
        raise ValueError("context_lag_schedule must be dense_geometric or consecutive.")
    return DataConfig(
        clickhouse_url=url,
        clickhouse_database=config.clickhouse_database,
        events_table=config.events_table,
        train_index_table=config.train_index_table,
        validation_index_table=config.validation_index_table,
        tickers=tuple(config.tickers),
        events_per_chunk=int(config.events_per_chunk),
        context_chunks=int(config.context_chunks),
        window_days=int(config.window_days),
        context_lag_schedule=str(config.context_lag_schedule),
        context_dense_fraction=float(config.context_dense_fraction),
        context_max_lag_steps=max(config.context_chunks - 1, int(config.context_max_lag_steps)),
        train_stride_choices=tuple(int(value) for value in config.train_stride_choices),
        validation_stride_choices=tuple(int(value) for value in config.validation_stride_choices),
        future_event_horizons=tuple(int(value) for value in config.future_event_horizons),
        origin_stride_events=max(1, int(config.origin_stride_events)),
        block_max_events=int(config.block_max_events),
        min_samples_per_block=int(config.min_samples_per_block),
        validation_blocks=int(config.validation_blocks),
        validation_batches_per_block=int(config.validation_batches_per_block),
        clickhouse_max_threads=int(config.clickhouse_max_threads),
        clickhouse_max_memory_usage=str(config.clickhouse_max_memory_usage),
        return_bps_scale=float(config.return_bps_scale),
    )


def required_event_lookback(config: DataConfig, stride: int) -> int:
    max_lag = max(context_lag_steps(config))
    return config.events_per_chunk + max_lag * int(stride) + max(config.future_event_horizons) + 1


def load_temporal_index_rows(client: ClickHouseHttpClient, config: DataConfig, *, split: str) -> list[TemporalIndexRow]:
    table = config.validation_index_table if split == "validation" else config.train_index_table
    tickers = tuple(value.upper() for value in config.tickers if value)
    ticker_filter = ""
    if tickers and tickers != ("ALL",) and "*" not in tickers:
        ticker_filter = "AND ticker IN (" + ", ".join(sql_string(value) for value in tickers) + ")"
    strides = config.validation_stride_choices if split == "validation" else config.train_stride_choices
    required = required_event_lookback(config, max(strides))
    query = f"""
SELECT
    ticker,
    first_ordinal,
    last_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    split_event_count
FROM {quote_ident(config.clickhouse_database)}.{quote_ident(table)}
WHERE split_event_count >= {int(required)}
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


def valid_origin_offsets(row_count: int, config: DataConfig, stride: int) -> np.ndarray:
    oldest_start = max(context_lag_steps(config)) * int(stride) + config.events_per_chunk - 1
    latest_origin = row_count - max(config.future_event_horizons) - 1
    if latest_origin < oldest_start:
        return np.empty((0,), dtype=np.int64)
    return np.arange(oldest_start, latest_origin + 1, max(1, config.origin_stride_events), dtype=np.int64)


def crop_random_event_subrange(rows: np.ndarray, config: DataConfig, stride: int, rng: random.Random) -> np.ndarray:
    required = required_event_lookback(config, stride)
    keep = max(required + config.min_samples_per_block, min(config.block_max_events, rows.shape[0]))
    if keep >= rows.shape[0]:
        return rows
    start = rng.randint(0, rows.shape[0] - keep)
    return rows[start : start + keep].copy()


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


def asof_quote_mid_price(rows: np.ndarray) -> np.ndarray:
    flags = rows["event_flags"].astype(np.uint8, copy=False)
    ask_price = decode_price_array(rows["price_primary_int"], flags & 1)
    bid_price = decode_price_array(rows["price_secondary_int"], (flags >> 1) & 1)
    is_quote = (
        (rows["event_type"] == QUOTE_EVENT_TYPE)
        & np.isfinite(ask_price)
        & np.isfinite(bid_price)
        & (ask_price > 0.0)
        & (bid_price > 0.0)
        & (ask_price >= bid_price)
    )
    quote_mid = np.full(rows.shape[0], np.nan, dtype=np.float64)
    quote_mid[is_quote] = (ask_price[is_quote] + bid_price[is_quote]) * 0.5
    quote_positions = np.where(np.isfinite(quote_mid), np.arange(rows.shape[0], dtype=np.int64), -1)
    np.maximum.accumulate(quote_positions, out=quote_positions)
    asof_mid = np.full(rows.shape[0], np.nan, dtype=np.float64)
    valid = quote_positions >= 0
    asof_mid[valid] = quote_mid[quote_positions[valid]]
    return asof_mid


def load_random_return_block(
    client: PersistentClickHouseBytesClient,
    index_rows: list[TemporalIndexRow],
    config: DataConfig,
    rng: random.Random,
) -> ReturnTemporalBlock | None:
    started = time.perf_counter()
    window_us = int(config.window_days * MICROSECONDS_PER_DAY)
    for _ in range(100):
        row = rng.choice(index_rows)
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
        origins = valid_origin_offsets(raw_rows.shape[0], config, stride)
        if origins.size < config.min_samples_per_block:
            continue
        mid_started = time.perf_counter()
        asof_mid = asof_quote_mid_price(raw_rows)
        valid_target_origins = filter_origins_with_valid_targets(origins, asof_mid, config)
        if valid_target_origins.size < config.min_samples_per_block:
            continue
        shuffle_numpy_in_place(valid_target_origins, rng)
        return ReturnTemporalBlock(
            ticker=row.ticker,
            stride=int(stride),
            rows=raw_rows,
            origins=valid_target_origins,
            asof_mid_price=asof_mid,
            profile={
                "data/block_load_seconds": time.perf_counter() - started,
                "data/block_query_seconds": query_seconds,
                "data/block_mid_seconds": time.perf_counter() - mid_started,
                "data/block_rows": float(raw_rows.shape[0]),
                "data/block_valid_origins": float(valid_target_origins.size),
                "data/context_stride_events": float(stride),
            },
        )
    return None


def filter_origins_with_valid_targets(origins: np.ndarray, asof_mid: np.ndarray, config: DataConfig) -> np.ndarray:
    if origins.size == 0:
        return origins
    origin_mid = asof_mid[origins]
    valid = np.isfinite(origin_mid) & (origin_mid > 0.0)
    for horizon in config.future_event_horizons:
        future_mid = asof_mid[origins + int(horizon)]
        valid &= np.isfinite(future_mid) & (future_mid > 0.0)
    return origins[valid]


def iter_block_batches(block: ReturnTemporalBlock, config: DataConfig, batch_size: int) -> Iterator[dict[str, object]]:
    offset = 0
    while offset < block.origins.size:
        batch, offset = materialize_next_return_batch(block, offset, config, batch_size)
        if batch is None:
            break
        yield batch


def materialize_next_return_batch(
    block: ReturnTemporalBlock,
    offset: int,
    config: DataConfig,
    batch_size: int,
) -> tuple[dict[str, object] | None, int]:
    started = time.perf_counter()
    context_headers = np.zeros((batch_size, config.context_chunks, HEADER_BYTES), dtype=np.uint8)
    context_events = np.zeros((batch_size, config.context_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    target_bps = np.zeros((batch_size, len(config.future_event_horizons)), dtype=np.float32)
    target_norm = np.zeros_like(target_bps)
    target_valid = np.zeros_like(target_bps, dtype=np.bool_)
    origin_ts = np.zeros((batch_size,), dtype=np.int64)
    origin_ordinals = np.zeros((batch_size,), dtype=np.int64)
    origin_mid = np.zeros((batch_size,), dtype=np.float32)
    filled = 0
    cursor = int(offset)
    rejected = 0
    while cursor < block.origins.size and filled < batch_size:
        origin = int(block.origins[cursor])
        cursor += 1
        encoded = encode_context_for_origin(block.rows, origin, config, block.stride)
        if encoded is None:
            rejected += 1
            continue
        returns_bps, valid = future_return_targets_bps(block.asof_mid_price, origin, config)
        if not bool(valid.all()):
            rejected += 1
            continue
        context_headers[filled] = encoded[0]
        context_events[filled] = encoded[1]
        target_bps[filled] = returns_bps
        target_norm[filled] = returns_bps / float(config.return_bps_scale)
        target_valid[filled] = valid
        origin_ts[filled] = int(block.rows["sip_timestamp_us"][origin]) * 1000
        origin_ordinals[filled] = int(block.rows["ordinal"][origin])
        origin_mid[filled] = float(block.asof_mid_price[origin])
        filled += 1
    if filled < batch_size:
        return None, cursor
    profile = dict(block.profile)
    profile.update(
        {
            "data/batch_materialize_seconds": time.perf_counter() - started,
            "data/batch_samples": float(batch_size),
            "data/rejected_origins_in_materializer": float(rejected),
        }
    )
    return (
        {
            "context_header_uint8": torch.from_numpy(context_headers),
            "context_events_uint8": torch.from_numpy(context_events),
            "target_return_bps": torch.from_numpy(target_bps),
            "target_return_norm": torch.from_numpy(target_norm),
            "target_valid_mask": torch.from_numpy(target_valid),
            "origin_timestamp_ns": torch.from_numpy(origin_ts),
            "origin_ordinal": torch.from_numpy(origin_ordinals),
            "origin_mid_price": torch.from_numpy(origin_mid),
            "ticker": [block.ticker] * batch_size,
            "profile": profile,
        },
        cursor,
    )


def encode_context_for_origin(rows: np.ndarray, origin: int, config: DataConfig, stride: int) -> tuple[np.ndarray, np.ndarray] | None:
    headers = np.zeros((config.context_chunks, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((config.context_chunks, config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    try:
        for chunk_idx, end in enumerate(context_end_offsets(int(origin), config, stride)):
            header, event_rows = encode_window_or_raise(rows, end - config.events_per_chunk + 1, end + 1)
            headers[chunk_idx] = header
            events[chunk_idx] = event_rows
        return headers, events
    except Exception:
        return None


def future_return_targets_bps(asof_mid: np.ndarray, origin: int, config: DataConfig) -> tuple[np.ndarray, np.ndarray]:
    origin_mid = float(asof_mid[int(origin)])
    values = np.zeros((len(config.future_event_horizons),), dtype=np.float32)
    valid = np.zeros_like(values, dtype=np.bool_)
    if not np.isfinite(origin_mid) or origin_mid <= 0.0:
        return values, valid
    for idx, horizon in enumerate(config.future_event_horizons):
        future_mid = float(asof_mid[int(origin) + int(horizon)])
        if np.isfinite(future_mid) and future_mid > 0.0:
            values[idx] = float((future_mid / origin_mid - 1.0) * 10_000.0)
            valid[idx] = True
    return values, valid


def build_fixed_validation_blocks(config: DataConfig, *, seed: int) -> list[TemporalValidationBlock]:
    normalized = normalized_data_config(config)
    rng = random.Random(seed)
    client = ClickHouseHttpClient(normalized.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
    rows = load_temporal_index_rows(client, normalized, split="validation")
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
            asof_mid = asof_quote_mid_price(rows)
            origins = filter_origins_with_valid_targets(origins, asof_mid, normalized)
            if origins.size == 0:
                continue
            shuffle_numpy_in_place(origins, rng)
            materialized = ReturnTemporalBlock(
                ticker=block.ticker,
                stride=block.stride,
                rows=rows,
                origins=origins,
                asof_mid_price=asof_mid,
                profile={},
            )
            for idx, batch in enumerate(iter_block_batches(materialized, normalized, batch_size)):
                if idx >= normalized.validation_batches_per_block:
                    break
                yield batch
    finally:
        client.close()


def context_end_offsets(origin: int, config: DataConfig, stride: int) -> list[int]:
    return [int(origin) - lag_step * int(stride) for lag_step in reversed(context_lag_steps(config))]


def context_lag_steps(config: DataConfig) -> tuple[int, ...]:
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
    selected: set[int] = set(dense)
    start = dense_count
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
        selected = set(dense) | set(choose_evenly_spaced(tail, n - dense_count))
    return tuple(sorted(selected))


def fill_missing_lags(selected: set[int], *, start: int, max_lag: int, target_count: int) -> None:
    candidates = [value for value in range(start, max_lag + 1) if value not in selected]
    if not candidates:
        return
    for value in choose_evenly_spaced(candidates, target_count - len(selected)):
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
