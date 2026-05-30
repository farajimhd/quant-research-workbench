from __future__ import annotations

import gc
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import polars as pl
import torch
from torch.utils.data import IterableDataset, get_worker_info

from research.masked_event_model.v2.config import DataConfig
from research.masked_event_model.v2.schema import (
    CHUNK_SUMMARY_COLUMNS,
    EVENT_KIND_PAD,
    LOG_COLUMNS,
    QUOTE_FEATURE_COLUMNS,
    QUOTE_PRICE_COLUMNS,
    SUMMARY_PRICE_COLUMNS,
    TARGET_PREFIX,
    TRADE_FEATURE_COLUMNS,
    TRADE_PRICE_COLUMNS,
)
from research.masked_event_model.v2.targets import encode_binary_magnitude_targets, log_return_bps


ALL_TICKERS = {"ALL", "*", "__ALL_TICKERS__"}


@dataclass(slots=True)
class ChunkFile:
    ticker: str
    year_month: str
    path: Path


def parse_tickers(raw: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    else:
        values = tuple(str(part).strip().upper() for part in raw if str(part).strip())
    return values or ("ALL",)


def uses_all_tickers(tickers: tuple[str, ...]) -> bool:
    return len(tickers) == 1 and tickers[0].upper() in ALL_TICKERS


def date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    out = []
    while start <= end:
        out.append(start.isoformat())
        start += timedelta(days=1)
    return out


def year_month_range(start_date: str, end_date: str) -> set[str]:
    return {value[:7] for value in date_range(start_date, end_date)}


def discover_chunk_files(config: DataConfig, *, start_date: str, end_date: str, tickers: tuple[str, ...] | None = None) -> list[ChunkFile]:
    months = year_month_range(start_date, end_date)
    selected = parse_tickers(tickers or config.tickers)
    root = chunk_layout_root(config)
    files: list[ChunkFile] = []
    if uses_all_tickers(selected):
        for path in sorted(root.glob("ticker=*/*.parquet")):
            year_month = path.stem
            if year_month not in months:
                continue
            ticker = path.parent.name.split("=", 1)[1].upper()
            files.append(ChunkFile(ticker=ticker, year_month=year_month, path=path))
    else:
        for ticker in selected:
            for year_month in sorted(months):
                path = root / f"ticker={ticker}" / f"{year_month}.parquet"
                if path.exists():
                    files.append(ChunkFile(ticker=ticker, year_month=year_month, path=path))
    if config.max_files > 0:
        files = files[: config.max_files]
    return files


def chunk_layout_root(config: DataConfig) -> Path:
    nested = config.cache_root / f"chunk_ms={config.chunk_ms}" / f"mq={config.max_quote_events}_mt={config.max_trade_events}_m={config.max_total_events}"
    if nested.exists():
        return nested
    return config.cache_root


def target_horizons_from_columns(columns: list[str]) -> tuple[int, ...]:
    values = []
    for column in columns:
        if column.startswith(TARGET_PREFIX):
            try:
                values.append(int(column.removeprefix(TARGET_PREFIX)))
            except ValueError:
                continue
    return tuple(sorted(set(values)))


def load_chunk_file(path: Path, *, start_date: str, end_date: str) -> pl.DataFrame:
    return (
        pl.scan_parquet(str(path))
        .filter((pl.col("session_date") >= start_date) & (pl.col("session_date") <= end_date))
        .sort(["session_date", "chunk_start_ns"])
        .collect()
    )


def chunk_scan(path: Path, *, start_date: str, end_date: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(path)).filter((pl.col("session_date") >= start_date) & (pl.col("session_date") <= end_date))


def count_chunk_rows(path: Path, *, start_date: str, end_date: str) -> int:
    return int(chunk_scan(path, start_date=start_date, end_date=end_date).select(pl.len()).collect().item())


def load_chunk_block(path: Path, *, start_date: str, end_date: str, row_offset: int, row_count: int) -> pl.DataFrame:
    return chunk_scan(path, start_date=start_date, end_date=end_date).slice(row_offset, row_count).collect()


def list_column_to_matrix(frame: pl.DataFrame, column: str, rows: int, cols: int) -> np.ndarray:
    if column not in frame.columns or frame.height == 0:
        return np.zeros((frame.height, rows, cols), dtype=np.float32)
    zero_rows = [[0.0] * cols for _ in range(rows)]
    values = frame.select(
        pl.concat_list([pl.col(column).fill_null([]), pl.lit(zero_rows)])
        .list.head(rows)
        .list.to_array(rows)
        .alias(column)
    )[column].to_numpy()
    values = np.stack(values.reshape(-1)).reshape(frame.height, rows, cols)
    return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)


def list_column_to_int_matrix(frame: pl.DataFrame, column: str, rows: int, fill: int = 0) -> np.ndarray:
    if column not in frame.columns or frame.height == 0:
        return np.full((frame.height, rows), fill, dtype=np.int64)
    values = frame.select(
        pl.concat_list([pl.col(column).fill_null([]), pl.lit([int(fill)] * rows)])
        .list.head(rows)
        .list.to_array(rows)
        .alias(column)
    )[column].to_numpy()
    return values.astype(np.int64, copy=False)


def mid_from_bid_ask(bid: Any, ask: Any, fallback_mid: Any) -> Any:
    bid_array = np.asarray(bid, dtype=np.float32)
    ask_array = np.asarray(ask, dtype=np.float32)
    fallback = np.asarray(fallback_mid, dtype=np.float32)
    quote_mid = (bid_array + ask_array) * 0.5
    return np.where((bid_array > 0.0) & (ask_array > 0.0), quote_mid, fallback)


def normalize_event_window(window: np.ndarray, columns: tuple[str, ...], price_columns: set[str], *, current_mid: float) -> np.ndarray:
    return normalize_event_windows(np.asarray(window, dtype=np.float32)[None, ...], columns, price_columns, current_mid=np.asarray([current_mid], dtype=np.float32))[0]


def normalize_event_windows(windows: np.ndarray, columns: tuple[str, ...], price_columns: set[str], *, current_mid: np.ndarray) -> np.ndarray:
    values = np.asarray(windows, dtype=np.float32).copy()
    current_mid_values = np.asarray(current_mid, dtype=np.float32).reshape(-1)
    current_mid_safe = np.maximum(current_mid_values, 1e-6).reshape((-1,) + (1,) * (values.ndim - 2))
    for index, column in enumerate(columns):
        column_values = values[..., index]
        if column in price_columns:
            safe = np.maximum(column_values, 1e-6)
            values[..., index] = np.where(column_values > 0.0, np.log(safe / current_mid_safe) * 10000.0, 0.0)
        elif column in LOG_COLUMNS or column.endswith("_count") or column.endswith("_volume"):
            values[..., index] = np.log1p(np.maximum(column_values, 0.0))
    flat = values.reshape(values.shape[0], -1, values.shape[-1])
    mean = flat.mean(axis=1)
    std_values = flat.std(axis=1)
    std = np.where(std_values > 1e-6, std_values, 1.0)
    reshape = (values.shape[0],) + (1,) * (values.ndim - 2) + (values.shape[-1],)
    normalized = (values - mean.reshape(reshape)) / std.reshape(reshape)
    return np.nan_to_num(normalized, nan=0.0, posinf=10.0, neginf=-10.0).astype(np.float32)


def dense_ticker_frame(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    target_horizons = target_horizons_from_columns(frame.columns)
    chunk_ns = config.chunk_ms * 1_000_000
    min_start = int(frame["chunk_start_ns"].min())
    max_start = int(frame["chunk_start_ns"].max())
    grid = (
        pl.DataFrame({"_grid": [0]})
        .select(pl.int_ranges(pl.lit(min_start), pl.lit(max_start + chunk_ns), pl.lit(chunk_ns)).alias("chunk_start_ns"))
        .explode("chunk_start_ns")
    )
    joined = grid.join(frame.drop(["ticker"], strict=False), on="chunk_start_ns", how="left")
    joined = joined.with_columns((pl.col("chunk_start_ns") + chunk_ns - 1).alias("chunk_end_ns"))
    quote_state_cols = ["latest_bid", "latest_ask", "latest_mid", "latest_spread_bps", "latest_bid_size", "latest_ask_size", "latest_quote_imbalance"]
    joined = joined.with_columns([pl.col(column).forward_fill() for column in quote_state_cols if column in joined.columns])
    joined = joined.filter(pl.col("latest_mid").is_not_null() & (pl.col("latest_mid") > 0.0))
    zero_cols = [column for column in CHUNK_SUMMARY_COLUMNS if column in joined.columns and column not in quote_state_cols and column not in {"seconds_since_trade", "seconds_since_quote"}]
    joined = joined.with_columns([pl.col(column).fill_null(0.0) for column in zero_cols])
    joined = joined.with_columns(
        pl.col("seconds_since_trade").fill_null(1e6),
        pl.col("seconds_since_quote").fill_null(1e6),
    )
    if target_horizons:
        joined = joined.with_columns(
            [
                pl.col("latest_mid").shift(-int(horizon)).alias(f"target_mid_h{horizon}")
                for horizon in target_horizons
            ]
        )
    for list_col in ("quote_values", "trade_values", "event_kinds", "event_indices"):
        if list_col not in joined.columns:
            joined = joined.with_columns(pl.lit(None).alias(list_col))
    return joined.select(
        [
            "session_date",
            "chunk_start_ns",
            "chunk_end_ns",
            "quote_values",
            "trade_values",
            "event_kinds",
            "event_indices",
            *CHUNK_SUMMARY_COLUMNS,
            *[f"target_mid_h{horizon}" for horizon in target_horizons],
        ]
    )


def prepare_loaded_chunk_frame(frame: pl.DataFrame, config: DataConfig) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    target_horizons = target_horizons_from_columns(frame.columns)
    chunk_ns = config.chunk_ms * 1_000_000
    if "chunk_end_ns" not in frame.columns:
        frame = frame.with_columns((pl.col("chunk_start_ns") + chunk_ns - 1).alias("chunk_end_ns"))
    for list_col in ("quote_values", "trade_values", "event_kinds", "event_indices"):
        if list_col not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(list_col))
    for column in CHUNK_SUMMARY_COLUMNS:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(0.0).alias(column))
    return frame.select(
        [
            "session_date",
            "chunk_start_ns",
            "chunk_end_ns",
            "quote_values",
            "trade_values",
            "event_kinds",
            "event_indices",
            *CHUNK_SUMMARY_COLUMNS,
            *[f"target_mid_h{horizon}" for horizon in target_horizons],
        ]
    )


def frame_to_arrays(frame: pl.DataFrame, config: DataConfig) -> dict[str, np.ndarray] | None:
    dense = prepare_loaded_chunk_frame(frame, config)
    if dense.height < config.context_chunks + 1:
        return None
    quote_values = list_column_to_matrix(dense, "quote_values", config.max_quote_events, len(QUOTE_FEATURE_COLUMNS))
    trade_values = list_column_to_matrix(dense, "trade_values", config.max_trade_events, len(TRADE_FEATURE_COLUMNS))
    event_kinds = list_column_to_int_matrix(dense, "event_kinds", config.max_total_events, fill=EVENT_KIND_PAD)
    event_indices = list_column_to_int_matrix(dense, "event_indices", config.max_total_events, fill=0)
    summary = dense.select(list(CHUNK_SUMMARY_COLUMNS)).to_numpy().astype(np.float32)
    horizons = target_horizons_from_columns(dense.columns)
    target_mid = np.stack([dense[f"target_mid_h{h}"].to_numpy().astype(np.float32) for h in horizons], axis=1) if horizons else np.zeros((dense.height, 0), dtype=np.float32)
    return {
        "chunk_end_ns": dense["chunk_end_ns"].to_numpy().astype(np.int64),
        "current_mid": dense["latest_mid"].to_numpy().astype(np.float32),
        "target_mid": target_mid,
        "target_horizons": np.asarray(horizons, dtype=np.int64),
        "quote_values": quote_values,
        "trade_values": trade_values,
        "event_kinds": event_kinds,
        "event_indices": event_indices,
        "chunk_summary": summary,
    }


class BatchBuilder:
    def __init__(self, config: DataConfig, batch_size: int, horizon_count: int) -> None:
        self.config = config
        context = config.context_chunks
        self.quote_values = np.empty((batch_size, context, config.max_quote_events, len(QUOTE_FEATURE_COLUMNS)), dtype=np.float32)
        self.trade_values = np.empty((batch_size, context, config.max_trade_events, len(TRADE_FEATURE_COLUMNS)), dtype=np.float32)
        self.event_kinds = np.empty((batch_size, context, config.max_total_events), dtype=np.int64)
        self.event_indices = np.empty((batch_size, context, config.max_total_events), dtype=np.int64)
        self.chunk_summary = np.empty((batch_size, context, len(CHUNK_SUMMARY_COLUMNS)), dtype=np.float32)
        self.targets = np.empty((batch_size, horizon_count, 1, config.target_bit_count), dtype=np.float32)
        self.target_bps = np.empty((batch_size, horizon_count, 1), dtype=np.float32)
        self.current_mid = np.empty((batch_size,), dtype=np.float32)
        self.origin_timestamp_ns = np.empty((batch_size,), dtype=np.int64)
        self.tickers: list[str] = [""] * batch_size
        self.count = 0

    @property
    def full(self) -> bool:
        return self.count >= self.quote_values.shape[0]

    def __len__(self) -> int:
        return self.count

    def add(self, arrays: dict[str, np.ndarray], origin: int, ticker: str) -> None:
        start = origin - self.config.context_chunks + 1
        end = origin + 1
        current_mid = float(arrays["current_mid"][origin])
        future_mid = arrays["target_mid"][origin].reshape(-1, 1).astype(np.float32)
        if future_mid.size == 0 or not np.all(np.isfinite(future_mid)):
            return
        target_bps = log_return_bps(future_mid, current_mid).astype(np.float32)
        self.quote_values[self.count] = normalize_event_window(arrays["quote_values"][start:end], QUOTE_FEATURE_COLUMNS, QUOTE_PRICE_COLUMNS, current_mid=current_mid)
        self.trade_values[self.count] = normalize_event_window(arrays["trade_values"][start:end], TRADE_FEATURE_COLUMNS, TRADE_PRICE_COLUMNS, current_mid=current_mid)
        self.chunk_summary[self.count] = normalize_event_window(arrays["chunk_summary"][start:end], CHUNK_SUMMARY_COLUMNS, SUMMARY_PRICE_COLUMNS, current_mid=current_mid)
        self.event_kinds[self.count] = arrays["event_kinds"][start:end]
        self.event_indices[self.count] = arrays["event_indices"][start:end]
        self.targets[self.count] = encode_binary_magnitude_targets(target_bps, bits=self.config.binary_magnitude_bits)
        self.target_bps[self.count] = target_bps
        self.current_mid[self.count] = current_mid
        self.origin_timestamp_ns[self.count] = int(arrays["chunk_end_ns"][origin])
        self.tickers[self.count] = ticker
        self.count += 1

    def add_many(self, arrays: dict[str, np.ndarray], origins: np.ndarray, ticker: str) -> int:
        if origins.size == 0 or self.full:
            return 0
        capacity = self.quote_values.shape[0] - self.count
        selected = np.asarray(origins[:capacity], dtype=np.int64)
        if selected.size == 0:
            return 0
        future_mid = arrays["target_mid"][selected].astype(np.float32)
        current_mid = arrays["current_mid"][selected].astype(np.float32)
        valid = (
            np.all(np.isfinite(future_mid) & (future_mid > 0.0), axis=1)
            & np.isfinite(current_mid)
            & (current_mid > 0.0)
        )
        selected = selected[valid]
        future_mid = future_mid[valid]
        current_mid = current_mid[valid]
        if selected.size == 0:
            return 0

        offsets = np.arange(self.config.context_chunks, dtype=np.int64)
        window_indices = selected[:, None] - self.config.context_chunks + 1 + offsets[None, :]
        target_bps = log_return_bps(future_mid[..., None], current_mid[:, None, None]).astype(np.float32)
        row_slice = slice(self.count, self.count + selected.size)
        self.quote_values[row_slice] = normalize_event_windows(
            arrays["quote_values"][window_indices],
            QUOTE_FEATURE_COLUMNS,
            QUOTE_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.trade_values[row_slice] = normalize_event_windows(
            arrays["trade_values"][window_indices],
            TRADE_FEATURE_COLUMNS,
            TRADE_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.chunk_summary[row_slice] = normalize_event_windows(
            arrays["chunk_summary"][window_indices],
            CHUNK_SUMMARY_COLUMNS,
            SUMMARY_PRICE_COLUMNS,
            current_mid=current_mid,
        )
        self.event_kinds[row_slice] = arrays["event_kinds"][window_indices]
        self.event_indices[row_slice] = arrays["event_indices"][window_indices]
        self.targets[row_slice] = encode_binary_magnitude_targets(target_bps, bits=self.config.binary_magnitude_bits)
        self.target_bps[row_slice] = target_bps
        self.current_mid[row_slice] = current_mid
        self.origin_timestamp_ns[row_slice] = arrays["chunk_end_ns"][selected]
        self.tickers[self.count : self.count + selected.size] = [ticker] * int(selected.size)
        self.count += int(selected.size)
        return int(selected.size)

    def as_torch(self) -> dict[str, Any]:
        rows = slice(0, self.count)
        return {
            "quote_values": torch.from_numpy(self.quote_values[rows]),
            "trade_values": torch.from_numpy(self.trade_values[rows]),
            "event_kinds": torch.from_numpy(self.event_kinds[rows]),
            "event_indices": torch.from_numpy(self.event_indices[rows]),
            "chunk_summary": torch.from_numpy(self.chunk_summary[rows]),
            "targets": torch.from_numpy(self.targets[rows]),
            "target_bps": torch.from_numpy(self.target_bps[rows]),
            "current_mid": torch.from_numpy(self.current_mid[rows]),
            "origin_timestamp_ns": torch.from_numpy(self.origin_timestamp_ns[rows]),
            "ticker": list(self.tickers[: self.count]),
        }


def valid_origins(arrays: dict[str, np.ndarray], config: DataConfig) -> np.ndarray:
    if arrays["target_mid"].shape[1] == 0:
        return np.asarray([], dtype=np.int64)
    start = config.context_chunks - 1
    valid_targets = np.all(np.isfinite(arrays["target_mid"]) & (arrays["target_mid"] > 0.0), axis=1)
    valid_current = np.isfinite(arrays["current_mid"]) & (arrays["current_mid"] > 0.0)
    valid = np.where(valid_targets & valid_current)[0]
    valid = valid[valid >= start]
    return valid[:: max(1, config.origin_stride_chunks)]


class EventChunkDataset(IterableDataset):
    def __init__(
        self,
        *,
        config: DataConfig,
        split: str,
        batch_size: int,
        seed: int = 17,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        shuffle: bool | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.split = split
        self.batch_size = batch_size
        self.seed = seed
        self.progress_callback = progress_callback
        self.shuffle = shuffle
        self.start_date, self.end_date = split_dates(config, split)
        self.files = discover_chunk_files(config, start_date=self.start_date, end_date=self.end_date)

    def progress(self, event: dict[str, Any]) -> None:
        message = event.pop("message", "")
        if message:
            print(message, flush=True)
        if self.progress_callback is not None:
            self.progress_callback(event)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        worker = get_worker_info()
        files = list(self.files)
        rng = random.Random(self.seed + (worker.id if worker else 0))
        shuffle = (self.config.shuffle_files and self.split == "train") if self.shuffle is None else self.shuffle
        if shuffle:
            rng.shuffle(files)
        if worker is not None:
            files = files[worker.id :: worker.num_workers]
        batch: BatchBuilder | None = None
        for file_info in files:
            total_rows = count_chunk_rows(file_info.path, start_date=self.start_date, end_date=self.end_date)
            first_origin = self.config.context_chunks - 1
            if total_rows <= first_origin:
                continue
            block_size = max(int(self.config.row_block_size), self.config.context_chunks)
            block_starts = list(range(first_origin, total_rows, block_size))
            if shuffle:
                rng.shuffle(block_starts)
            self.progress(
                {
                    "message": f"{self.split} file {file_info.ticker}:{file_info.year_month} rows={total_rows:,} blocks={len(block_starts):,}",
                    "loader/event": "file_start",
                    "loader/split": self.split,
                    "loader/ticker": file_info.ticker,
                    "loader/year_month": file_info.year_month,
                    "loader/file_rows": total_rows,
                    "loader/file_blocks": len(block_starts),
                }
            )
            windows_from_file = 0
            for origin_start in block_starts:
                block_started = time.perf_counter()
                if self.config.max_windows_per_file > 0 and windows_from_file >= self.config.max_windows_per_file:
                    break
                origin_end = min(origin_start + block_size, total_rows)
                row_start = max(0, origin_start - self.config.context_chunks + 1)
                row_count = origin_end - row_start
                frame = load_chunk_block(
                    file_info.path,
                    start_date=self.start_date,
                    end_date=self.end_date,
                    row_offset=row_start,
                    row_count=row_count,
                )
                arrays = frame_to_arrays(frame, self.config)
                del frame
                if arrays is None:
                    continue
                local_start = origin_start - row_start
                local_end = origin_end - row_start
                origins = valid_origins(arrays, self.config)
                origins = origins[(origins >= local_start) & (origins < local_end)]
                if shuffle:
                    np_rng = np.random.default_rng(self.seed + origin_start + len(file_info.ticker) + len(file_info.year_month))
                    np_rng.shuffle(origins)
                if self.config.max_windows_per_file > 0:
                    remaining = self.config.max_windows_per_file - windows_from_file
                    origins = origins[:remaining]
                if batch is None:
                    batch = BatchBuilder(self.config, self.batch_size, int(arrays["target_mid"].shape[1]))
                cursor = 0
                last_progress = 0
                while cursor < origins.size:
                    added = batch.add_many(arrays, origins[cursor:], file_info.ticker)
                    if added <= 0:
                        break
                    cursor += added
                    windows_from_file += added
                    if windows_from_file - last_progress >= self.config.loader_progress_windows:
                        last_progress = windows_from_file
                        self.progress(
                            {
                                "message": (
                                    f"{self.split} block {file_info.ticker}:{file_info.year_month} "
                                    f"origin={origin_start:,}-{origin_end:,} windows={windows_from_file:,} "
                                    f"batch_fill={len(batch):,}/{self.batch_size}"
                                ),
                                "loader/event": "block_progress",
                                "loader/split": self.split,
                                "loader/ticker": file_info.ticker,
                                "loader/year_month": file_info.year_month,
                                "loader/windows_from_file": windows_from_file,
                                "loader/batch_fill": len(batch),
                                "loader/batch_size": self.batch_size,
                            }
                        )
                    if batch.full:
                        yield batch.as_torch()
                        batch = BatchBuilder(self.config, self.batch_size, int(arrays["target_mid"].shape[1]))
                self.progress(
                    {
                        "message": (
                            f"{self.split} block_done {file_info.ticker}:{file_info.year_month} "
                            f"origin={origin_start:,}-{origin_end:,} valid_origins={origins.size:,} "
                            f"seconds={time.perf_counter() - block_started:.1f}"
                        ),
                        "loader/event": "block_done",
                        "loader/split": self.split,
                        "loader/ticker": file_info.ticker,
                        "loader/year_month": file_info.year_month,
                        "loader/block_seconds": time.perf_counter() - block_started,
                        "loader/block_valid_origins": int(origins.size),
                    }
                )
                del arrays
                gc.collect()
        if batch is not None and len(batch) > 0:
            yield batch.as_torch()


def split_dates(config: DataConfig, split: str) -> tuple[str, str]:
    if split == "train":
        return config.train_start_date, config.train_end_date
    if split in {"val", "validation"}:
        return config.validation_start_date, config.validation_end_date
    if split == "test":
        return config.test_start_date, config.test_end_date
    raise ValueError(f"Unknown split: {split}")
