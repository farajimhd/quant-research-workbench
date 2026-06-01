from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import polars as pl
import torch
from torch.utils.data import IterableDataset, get_worker_info


ALL_TICKERS = {"ALL", "*", "__ALL_TICKERS__"}
NANOSECONDS_PER_MICROSECOND = 1_000
NANOSECONDS_PER_DAY = 24 * 60 * 60 * 1_000_000_000

DEFAULT_CANONICAL_ROOT = Path("D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_compact_v1")
DEFAULT_REFERENCE_DIR = Path(__file__).resolve().parents[1] / "market_references" / "massive"

HEADER_BYTES = 14
EVENT_BYTES = 16
DEFAULT_EVENTS_PER_CHUNK = 128

QUOTE_EVENT_TYPE = 0
TRADE_EVENT_TYPE = 1

QUOTE_COLUMNS = (
    "ticker",
    "session_date",
    "year_month",
    "sip_timestamp",
    "sequence_number",
    "bid_price",
    "ask_price",
    "bid_size",
    "ask_size",
    "bid_exchange",
    "ask_exchange",
    "tape",
    "condition_count",
    "condition_first",
    "condition_1",
    "condition_2",
    "condition_3",
    "condition_4",
)

TRADE_COLUMNS = (
    "ticker",
    "session_date",
    "year_month",
    "sip_timestamp",
    "sequence_number",
    "price",
    "size",
    "exchange",
    "tape",
    "condition_count",
    "condition_first",
    "condition_1",
    "condition_2",
    "condition_3",
    "condition_4",
    "correction",
)


@dataclass(frozen=True, slots=True)
class CompactEventDataConfig:
    canonical_root: Path = DEFAULT_CANONICAL_ROOT
    reference_dir: Path = DEFAULT_REFERENCE_DIR
    start_date: str = "2025-11-01"
    end_date: str = "2025-11-30"
    tickers: tuple[str, ...] = ("ALL",)
    events_per_chunk: int = DEFAULT_EVENTS_PER_CHUNK
    batch_size: int = 256
    seed: int = 17
    max_index_files: int = 0
    max_sample_attempts_per_batch: int = 20
    month_cache_size: int = 8
    sample_mode: str = "session_time_ticker"
    strict_lossless: bool = True

    @property
    def row_bytes(self) -> int:
        return HEADER_BYTES + self.events_per_chunk * EVENT_BYTES


@dataclass(frozen=True, slots=True)
class PrecomputedChunkDataConfig:
    chunk_root: Path
    start_date: str
    end_date: str
    batch_size: int = 256
    events_per_chunk: int = DEFAULT_EVENTS_PER_CHUNK
    seed: int = 17
    shard_cache_size: int = 4


@dataclass(frozen=True, slots=True)
class CanonicalGroup:
    ticker: str
    year_month: str
    quote_path: Path | None
    trade_path: Path | None


@dataclass(frozen=True, slots=True)
class AvailabilityRow:
    ticker: str
    year_month: str
    session_date: str
    min_ts: int
    max_ts: int
    event_count: int


@dataclass(slots=True)
class ReferenceMaps:
    exchange: dict[int, int] = field(default_factory=dict)
    condition: dict[int, int] = field(default_factory=dict)
    tape: dict[int, int] = field(default_factory=dict)

    @classmethod
    def load(cls, reference_dir: Path = DEFAULT_REFERENCE_DIR) -> "ReferenceMaps":
        return cls(
            exchange=load_dense_id_map(reference_dir / "stock_exchanges.json"),
            condition=load_dense_id_map(reference_dir / "stock_conditions.json"),
            tape=load_dense_id_map(reference_dir / "stock_tapes.json"),
        )

    def exchange_id(self, value: Any) -> int:
        return dense_lookup(self.exchange, value)

    def condition_id(self, value: Any) -> int:
        return dense_lookup(self.condition, value)

    def tape_id(self, value: Any) -> int:
        return dense_lookup(self.tape, value)


def load_dense_id_map(path: Path) -> dict[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, int] = {}
    for row in payload.get("results", []):
        raw_id = row.get("id")
        dense_id = row.get("dense_id")
        if raw_id is None or dense_id is None:
            continue
        out[int(raw_id)] = int(dense_id)
    return out


def dense_lookup(mapping: dict[int, int], value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        return int(mapping.get(int(value), 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_tickers(raw: str | Iterable[str]) -> tuple[str, ...]:
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
    values: list[str] = []
    while start <= end:
        values.append(start.isoformat())
        start += timedelta(days=1)
    return values


def year_month_range(start_date: str, end_date: str) -> set[str]:
    return {value[:7] for value in date_range(start_date, end_date)}


def previous_year_month(year_month: str) -> str:
    year, month = (int(part) for part in year_month.split("-", 1))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def canonical_event_path(canonical_root: Path, kind: str, ticker: str, year_month: str) -> Path:
    return canonical_root / kind / f"ticker={ticker}" / f"{year_month}.parquet"


def discover_canonical_groups(config: CompactEventDataConfig) -> list[CanonicalGroup]:
    months = year_month_range(config.start_date, config.end_date)
    tickers = parse_tickers(config.tickers)
    groups: dict[tuple[str, str], dict[str, Path]] = {}
    if uses_all_tickers(tickers):
        for kind in ("quotes", "trades"):
            base = config.canonical_root / kind
            if not base.exists():
                continue
            for path in sorted(base.glob("ticker=*/*.parquet")):
                year_month = path.stem
                if year_month not in months:
                    continue
                ticker = path.parent.name.split("=", 1)[1].upper()
                groups.setdefault((ticker, year_month), {})[kind] = path
    else:
        for ticker in tickers:
            for year_month in months:
                quote_path = canonical_event_path(config.canonical_root, "quotes", ticker, year_month)
                trade_path = canonical_event_path(config.canonical_root, "trades", ticker, year_month)
                if quote_path.exists() or trade_path.exists():
                    values: dict[str, Path] = {}
                    if quote_path.exists():
                        values["quotes"] = quote_path
                    if trade_path.exists():
                        values["trades"] = trade_path
                    groups[(ticker, year_month)] = values
    result = [
        CanonicalGroup(ticker=ticker, year_month=year_month, quote_path=paths.get("quotes"), trade_path=paths.get("trades"))
        for (ticker, year_month), paths in sorted(groups.items())
    ]
    if config.max_index_files > 0:
        result = result[: config.max_index_files]
    return result


def scan_group_events(group: CanonicalGroup, *, include_previous_month: bool = False, canonical_root: Path | None = None) -> pl.LazyFrame:
    frames: list[pl.LazyFrame] = []
    paths: list[tuple[str, Path]] = []
    if include_previous_month and canonical_root is not None:
        prev = previous_year_month(group.year_month)
        prev_quote = canonical_event_path(canonical_root, "quotes", group.ticker, prev)
        prev_trade = canonical_event_path(canonical_root, "trades", group.ticker, prev)
        if prev_quote.exists():
            paths.append(("quotes", prev_quote))
        if prev_trade.exists():
            paths.append(("trades", prev_trade))
    if group.quote_path is not None:
        paths.append(("quotes", group.quote_path))
    if group.trade_path is not None:
        paths.append(("trades", group.trade_path))
    for kind, path in paths:
        if kind == "quotes":
            frames.append(scan_quote_events(path))
        else:
            frames.append(scan_trade_events(path))
    if not frames:
        return pl.LazyFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort(["sip_timestamp", "sequence_number", "event_type"])


def scan_quote_events(path: Path) -> pl.LazyFrame:
    names = set(pl.scan_parquet(str(path)).collect_schema().names())
    return (
        pl.scan_parquet(str(path))
        .select([pl.col(column) for column in QUOTE_COLUMNS if column in names])
        .with_columns(
            pl.lit(QUOTE_EVENT_TYPE).cast(pl.UInt8).alias("event_type"),
            pl.col("bid_price").cast(pl.Float64, strict=False),
            pl.col("ask_price").cast(pl.Float64, strict=False),
            pl.col("bid_size").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("ask_size").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("bid_exchange").cast(pl.Int32, strict=False).fill_null(0),
            pl.col("ask_exchange").cast(pl.Int32, strict=False).fill_null(0),
            pl.lit(None, dtype=pl.Float64).alias("price"),
            pl.lit(None, dtype=pl.Float64).alias("size"),
            pl.lit(0, dtype=pl.Int32).alias("exchange"),
            pl.lit(0, dtype=pl.Int32).alias("correction"),
            optional_condition_first(names),
            optional_int_column("condition_1", names, fallback="condition_first"),
            optional_int_column("condition_2", names),
            optional_int_column("condition_3", names),
            optional_int_column("condition_4", names),
        )
        .select(unified_event_columns())
    )


def scan_trade_events(path: Path) -> pl.LazyFrame:
    names = set(pl.scan_parquet(str(path)).collect_schema().names())
    return (
        pl.scan_parquet(str(path))
        .select([pl.col(column) for column in TRADE_COLUMNS if column in names])
        .with_columns(
            pl.lit(TRADE_EVENT_TYPE).cast(pl.UInt8).alias("event_type"),
            pl.col("price").cast(pl.Float64, strict=False),
            pl.col("size").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("exchange").cast(pl.Int32, strict=False).fill_null(0),
            pl.col("correction").cast(pl.Int32, strict=False).fill_null(0),
            pl.lit(None, dtype=pl.Float64).alias("bid_price"),
            pl.lit(None, dtype=pl.Float64).alias("ask_price"),
            pl.lit(None, dtype=pl.Float64).alias("bid_size"),
            pl.lit(None, dtype=pl.Float64).alias("ask_size"),
            pl.lit(0, dtype=pl.Int32).alias("bid_exchange"),
            pl.lit(0, dtype=pl.Int32).alias("ask_exchange"),
            optional_condition_first(names),
            optional_int_column("condition_1", names, fallback="condition_first"),
            optional_int_column("condition_2", names),
            optional_int_column("condition_3", names),
            optional_int_column("condition_4", names),
        )
        .select(unified_event_columns())
    )


def optional_int_column(column: str, names: set[str], *, fallback: str | None = None) -> pl.Expr:
    if column in names:
        return pl.col(column).cast(pl.Int32, strict=False).fill_null(0).alias(column)
    if fallback and fallback in names:
        return pl.col(fallback).cast(pl.Int32, strict=False).fill_null(0).alias(column)
    return pl.lit(0, dtype=pl.Int32).alias(column)


def optional_condition_first(names: set[str]) -> pl.Expr:
    if "condition_first" in names:
        return pl.col("condition_first").cast(pl.Int32, strict=False).fill_null(0).alias("condition_first")
    if "condition_1" in names:
        return pl.col("condition_1").cast(pl.Int32, strict=False).fill_null(0).alias("condition_first")
    return pl.lit(0, dtype=pl.Int32).alias("condition_first")


def unified_event_columns() -> list[pl.Expr]:
    return [
        pl.col("ticker").cast(pl.String),
        pl.col("session_date").cast(pl.String),
        pl.col("year_month").cast(pl.String),
        pl.col("sip_timestamp").cast(pl.Int64),
        pl.col("sequence_number").cast(pl.Int64, strict=False).fill_null(0),
        pl.col("event_type").cast(pl.UInt8),
        pl.col("bid_price"),
        pl.col("ask_price"),
        pl.col("bid_size"),
        pl.col("ask_size"),
        pl.col("bid_exchange"),
        pl.col("ask_exchange"),
        pl.col("price"),
        pl.col("size"),
        pl.col("exchange"),
        pl.col("tape").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_count").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_first").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_1").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_2").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_3").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("condition_4").cast(pl.Int32, strict=False).fill_null(0),
        pl.col("correction"),
    ]


def build_availability_index(config: CompactEventDataConfig) -> list[AvailabilityRow]:
    rows: list[AvailabilityRow] = []
    for group in discover_canonical_groups(config):
        frame = (
            scan_group_events(group)
            .filter((pl.col("session_date") >= config.start_date) & (pl.col("session_date") <= config.end_date))
            .group_by("session_date")
            .agg(
                pl.col("sip_timestamp").min().alias("min_ts"),
                pl.col("sip_timestamp").max().alias("max_ts"),
                pl.len().alias("event_count"),
            )
            .filter(pl.col("event_count") >= config.events_per_chunk)
            .collect()
        )
        for item in frame.iter_rows(named=True):
            rows.append(
                AvailabilityRow(
                    ticker=group.ticker,
                    year_month=group.year_month,
                    session_date=str(item["session_date"]),
                    min_ts=int(item["min_ts"]),
                    max_ts=int(item["max_ts"]),
                    event_count=int(item["event_count"]),
                )
            )
    if not rows:
        raise FileNotFoundError(f"No canonical event availability found under {config.canonical_root}")
    return rows


class TickerMonthCache:
    def __init__(self, config: CompactEventDataConfig) -> None:
        self.config = config
        self.cache: dict[tuple[str, str], pl.DataFrame] = {}
        self.order: list[tuple[str, str]] = []
        self.last_get_seconds = 0.0
        self.last_cache_hit = False

    def get(self, ticker: str, year_month: str) -> pl.DataFrame:
        started = time.perf_counter()
        key = (ticker, year_month)
        if key in self.cache:
            self.last_get_seconds = time.perf_counter() - started
            self.last_cache_hit = True
            return self.cache[key]
        quote_path = canonical_event_path(self.config.canonical_root, "quotes", ticker, year_month)
        trade_path = canonical_event_path(self.config.canonical_root, "trades", ticker, year_month)
        group = CanonicalGroup(
            ticker=ticker,
            year_month=year_month,
            quote_path=quote_path if quote_path.exists() else None,
            trade_path=trade_path if trade_path.exists() else None,
        )
        frame = scan_group_events(group, include_previous_month=True, canonical_root=self.config.canonical_root).collect()
        if len(self.cache) >= self.config.month_cache_size and self.order:
            oldest = self.order.pop(0)
            self.cache.pop(oldest, None)
        self.cache[key] = frame
        self.order.append(key)
        self.last_get_seconds = time.perf_counter() - started
        self.last_cache_hit = False
        return frame


class CompactEventIterableDataset(IterableDataset):
    def __init__(
        self,
        config: CompactEventDataConfig,
        *,
        availability: list[AvailabilityRow] | None = None,
        references: ReferenceMaps | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.availability = availability or build_availability_index(config)
        self.references = references or ReferenceMaps.load(config.reference_dir)
        self.sessions = sorted({row.session_date for row in self.availability})
        self.by_session: dict[str, list[AvailabilityRow]] = {
            session: [row for row in self.availability if row.session_date == session]
            for session in self.sessions
        }

    def __iter__(self) -> Iterator[dict[str, Any]]:
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.config.seed + worker_id)
        cache = TickerMonthCache(self.config)
        while True:
            yield self._next_batch(rng, cache)

    def _next_batch(self, rng: random.Random, cache: TickerMonthCache) -> dict[str, Any]:
        batch_started = time.perf_counter()
        batch_session = rng.choice(self.sessions)
        session_rows = self.by_session[batch_session]
        session_min = min(row.min_ts for row in session_rows)
        session_max = max(row.max_ts for row in session_rows)
        headers = np.zeros((self.config.batch_size, HEADER_BYTES), dtype=np.uint8)
        events = np.zeros((self.config.batch_size, self.config.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
        origin_ts = np.zeros((self.config.batch_size,), dtype=np.int64)
        tickers: list[str] = []
        sessions: list[str] = []
        filled = 0
        attempts = 0
        sample_seconds = 0.0
        cache_seconds = 0.0
        encode_seconds = 0.0
        cache_hits = 0
        cache_misses = 0
        rejected_samples = 0
        max_attempts = max(self.config.batch_size, self.config.batch_size * self.config.max_sample_attempts_per_batch)
        while filled < self.config.batch_size and attempts < max_attempts:
            attempts += 1
            sample_started = time.perf_counter()
            row, timestamp = self._sample_origin_from_session(
                rng,
                session_rows=session_rows,
                session_min=session_min,
                session_max=session_max,
            )
            sample_seconds += time.perf_counter() - sample_started
            frame = cache.get(row.ticker, row.year_month)
            cache_seconds += cache.last_get_seconds
            if cache.last_cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
            encode_started = time.perf_counter()
            encoded = encode_events_chunk_from_frame(
                frame,
                origin_timestamp_ns=timestamp,
                events_per_chunk=self.config.events_per_chunk,
                references=self.references,
                strict_lossless=self.config.strict_lossless,
            )
            encode_seconds += time.perf_counter() - encode_started
            if encoded is None:
                rejected_samples += 1
                continue
            headers[filled] = encoded[0]
            events[filled] = encoded[1]
            origin_ts[filled] = timestamp
            tickers.append(row.ticker)
            sessions.append(row.session_date)
            filled += 1
        if filled == 0:
            raise RuntimeError("Could not draw any valid compact event samples from canonical data.")
        return {
            "header_uint8": torch.from_numpy(headers[:filled]),
            "events_uint8": torch.from_numpy(events[:filled]),
            "origin_timestamp_ns": torch.from_numpy(origin_ts[:filled]),
            "ticker": tickers,
            "session_date": sessions,
            "batch_session_date": batch_session,
            "row_bytes": self.config.row_bytes,
            "events_per_chunk": self.config.events_per_chunk,
            "profile": {
                "data/batch_build_seconds": time.perf_counter() - batch_started,
                "data/sample_select_seconds": sample_seconds,
                "data/cache_get_seconds": cache_seconds,
                "data/encode_seconds": encode_seconds,
                "data/attempts": float(attempts),
                "data/filled": float(filled),
                "data/rejected_samples": float(rejected_samples),
                "data/cache_hits": float(cache_hits),
                "data/cache_misses": float(cache_misses),
                "data/cache_hit_pct": 100.0 * float(cache_hits) / max(1.0, float(cache_hits + cache_misses)),
            },
        }

    def _sample_origin_from_session(
        self,
        rng: random.Random,
        *,
        session_rows: list[AvailabilityRow],
        session_min: int,
        session_max: int,
    ) -> tuple[AvailabilityRow, int]:
        if self.config.sample_mode == "event_weighted":
            row = rng.choices(session_rows, weights=[max(1, item.event_count) for item in session_rows], k=1)[0]
            return row, rng.randint(row.min_ts, row.max_ts)
        timestamp = rng.randint(session_min, session_max)
        available = [row for row in session_rows if row.min_ts <= timestamp <= row.max_ts]
        if not available:
            row = rng.choice(session_rows)
            timestamp = rng.randint(row.min_ts, row.max_ts)
            return row, timestamp
        return rng.choice(available), timestamp


class PrecomputedV4ChunkIterableDataset(IterableDataset):
    def __init__(self, config: PrecomputedChunkDataConfig) -> None:
        super().__init__()
        self.config = config
        self.shards = discover_precomputed_chunk_shards(config)
        if not self.shards:
            raise FileNotFoundError(f"No precomputed v4 chunk shards found under {config.chunk_root}")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.config.seed + worker_id)
        cache: dict[Path, pl.DataFrame] = {}
        order: list[Path] = []
        while True:
            started = time.perf_counter()
            shard = rng.choice(self.shards)
            frame = get_cached_chunk_shard(shard, cache, order, self.config.shard_cache_size)
            frame = frame.filter((pl.col("origin_session_date") >= self.config.start_date) & (pl.col("origin_session_date") <= self.config.end_date))
            if frame.height == 0:
                continue
            count = min(self.config.batch_size, frame.height)
            indices = [rng.randrange(frame.height) for _ in range(count)]
            batch = frame[indices]
            headers = np.stack([np.frombuffer(value, dtype=np.uint8, count=HEADER_BYTES) for value in batch["header_uint8"].to_list()])
            event_values = batch["events_uint8"].to_list()
            events = np.stack(
                [
                    np.frombuffer(value, dtype=np.uint8, count=self.config.events_per_chunk * EVENT_BYTES).reshape(self.config.events_per_chunk, EVENT_BYTES)
                    for value in event_values
                ]
            )
            yield {
                "header_uint8": torch.from_numpy(headers.copy()),
                "events_uint8": torch.from_numpy(events.copy()),
                "origin_timestamp_ns": torch.from_numpy(batch["origin_timestamp_ns"].to_numpy().astype(np.int64)),
                "ticker": batch["ticker"].to_list(),
                "session_date": batch["origin_session_date"].to_list(),
                "batch_session_date": "precomputed",
                "row_bytes": HEADER_BYTES + self.config.events_per_chunk * EVENT_BYTES,
                "events_per_chunk": self.config.events_per_chunk,
                "profile": {
                    "data/batch_build_seconds": time.perf_counter() - started,
                    "data/sample_select_seconds": 0.0,
                    "data/cache_get_seconds": 0.0,
                    "data/encode_seconds": 0.0,
                    "data/attempts": float(count),
                    "data/filled": float(count),
                    "data/rejected_samples": 0.0,
                    "data/cache_hits": 0.0,
                    "data/cache_misses": 0.0,
                    "data/cache_hit_pct": 100.0,
                },
            }


def discover_precomputed_chunk_shards(config: PrecomputedChunkDataConfig) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(config.chunk_root.glob("bucket=*/part-*.parquet")):
        try:
            stats = pl.scan_parquet(str(path)).select(
                pl.col("origin_session_date").min().alias("min_date"),
                pl.col("origin_session_date").max().alias("max_date"),
            ).collect()
        except Exception:
            continue
        if stats.height == 0:
            continue
        min_date = str(stats["min_date"][0])
        max_date = str(stats["max_date"][0])
        if max_date >= config.start_date and min_date <= config.end_date:
            paths.append(path)
    return paths


def get_cached_chunk_shard(path: Path, cache: dict[Path, pl.DataFrame], order: list[Path], cache_size: int) -> pl.DataFrame:
    if path in cache:
        return cache[path]
    frame = pl.read_parquet(path)
    if len(cache) >= max(1, cache_size) and order:
        oldest = order.pop(0)
        cache.pop(oldest, None)
    cache[path] = frame
    order.append(path)
    return frame


def encode_events_chunk_from_frame(
    frame: pl.DataFrame,
    *,
    origin_timestamp_ns: int,
    events_per_chunk: int,
    references: ReferenceMaps,
    strict_lossless: bool = True,
) -> tuple[np.ndarray, np.ndarray] | None:
    if frame.height < events_per_chunk:
        return None
    timestamps = frame["sip_timestamp"].to_numpy()
    origin_idx = int(np.searchsorted(timestamps, origin_timestamp_ns, side="right") - 1)
    start_idx = origin_idx - events_per_chunk + 1
    if start_idx < 0:
        return None
    quote_mask_all = frame["event_type"].to_numpy() == QUOTE_EVENT_TYPE
    quote_candidates = np.flatnonzero(quote_mask_all[: origin_idx + 1])
    if quote_candidates.size == 0:
        return None
    anchor_idx = int(quote_candidates[-1])
    anchor_ask = as_float(frame["ask_price"][anchor_idx])
    anchor_bid = as_float(frame["bid_price"][anchor_idx])
    if anchor_ask <= 0.0 or anchor_bid <= 0.0 or anchor_ask < anchor_bid:
        return None
    tick_size = 0.01 if anchor_ask >= 1.0 else 0.0001
    ask_anchor_ticks = int(round(anchor_ask / tick_size))
    spread_anchor_ticks = int(round((anchor_ask - anchor_bid) / tick_size))
    if ask_anchor_ticks >= 2**20 or spread_anchor_ticks >= 2**16:
        return None

    window = frame.slice(start_idx, events_per_chunk)
    event_types = window["event_type"].to_numpy().astype(np.uint8)
    event_ts = window["sip_timestamp"].to_numpy().astype(np.int64)
    event_deltas_us = np.zeros((events_per_chunk,), dtype=np.int64)
    event_deltas_us[1:] = np.maximum(0, (event_ts[1:] - event_ts[:-1]) // NANOSECONDS_PER_MICROSECOND)

    header = np.zeros((HEADER_BYTES,), dtype=np.uint8)
    put_uint_le(header, 0, ask_anchor_ticks, 3)
    header[2] &= 0x0F
    put_uint_le(header, 3, spread_anchor_ticks, 2)
    put_uint_le(header, 5, log_time_bucket(int((event_ts[-1] - event_ts[0]) // NANOSECONDS_PER_MICROSECOND)), 2)
    put_uint_le(header, 7, 0, 2)
    start_gap_us = 0
    if start_idx > 0:
        previous_ts = int(timestamps[start_idx - 1])
        start_gap_us = max(0, int((event_ts[0] - previous_ts) // NANOSECONDS_PER_MICROSECOND))
    put_uint_le(header, 9, log_time_bucket(start_gap_us), 2)
    quote_count = int(np.count_nonzero(event_types == QUOTE_EVENT_TYPE))
    trade_count = int(np.count_nonzero(event_types == TRADE_EVENT_TYPE))
    if quote_count > 255 or trade_count > 255:
        return None
    header[11] = quote_count
    header[12] = trade_count
    header[13] = 0x01 | (0x02 if trade_count > 0 else 0) | (0x04 if tick_size == 0.01 else 0)

    events = np.zeros((events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    for row_idx in range(events_per_chunk):
        event = window.row(row_idx, named=True)
        encoded = encode_event_row(event, event_delta_us=int(event_deltas_us[row_idx]), ask_anchor_ticks=ask_anchor_ticks, spread_anchor_ticks=spread_anchor_ticks, tick_size=tick_size, references=references)
        if encoded is None:
            if strict_lossless:
                return None
            continue
        events[row_idx] = encoded
    return header, events


def encode_event_row(
    event: dict[str, Any],
    *,
    event_delta_us: int,
    ask_anchor_ticks: int,
    spread_anchor_ticks: int,
    tick_size: float,
    references: ReferenceMaps,
) -> np.ndarray | None:
    out = np.zeros((EVENT_BYTES,), dtype=np.uint8)
    event_type = int(event["event_type"])
    correction = correction_code(event.get("correction", 0)) if event_type == TRADE_EVENT_TYPE else 0
    out[0] = np.uint8((event_type & 0x01) | 0x02 | ((correction & 0x0F) << 2))
    put_uint_le(out, 1, log_time_bucket(event_delta_us), 2)
    if event_type == QUOTE_EVENT_TYPE:
        ask = as_float(event.get("ask_price"))
        bid = as_float(event.get("bid_price"))
        if ask <= 0.0 or bid <= 0.0 or ask < bid:
            return None
        ask_ticks = int(round(ask / tick_size))
        spread_ticks = int(round((ask - bid) / tick_size))
        price_1 = ask_ticks - ask_anchor_ticks
        price_2 = spread_ticks - spread_anchor_ticks
        size_1 = as_float(event.get("bid_size"))
        size_2 = as_float(event.get("ask_size"))
        exchange_1 = references.exchange_id(event.get("bid_exchange"))
        exchange_2 = references.exchange_id(event.get("ask_exchange"))
    else:
        price = as_float(event.get("price"))
        if price <= 0.0:
            return None
        trade_ticks = int(round(price / tick_size))
        price_1 = trade_ticks - ask_anchor_ticks
        price_2 = 0
        size_1 = as_float(event.get("size"))
        size_2 = 0.0
        exchange_1 = references.exchange_id(event.get("exchange"))
        exchange_2 = 0
    if not (-32768 <= price_1 <= 32767 and -32768 <= price_2 <= 32767):
        return None
    put_int16_le(out, 3, price_1)
    put_int16_le(out, 5, price_2)
    out[7] = size_bucket(size_1)
    out[8] = size_bucket(size_2)
    tape_id = references.tape_id(event.get("tape"))
    out[9] = np.uint8((1 if 0.0 < size_1 < 100.0 else 0) | ((1 if 0.0 < size_2 < 100.0 else 0) << 1) | ((tape_id & 0x07) << 2))
    out[10] = np.uint8(exchange_1 & 0x1F)
    out[11] = np.uint8(exchange_2 & 0x1F)
    for slot in range(4):
        condition = references.condition_id(event.get(f"condition_{slot + 1}"))
        if condition:
            out[12 + slot] = np.uint8(0x80 | (condition & 0x7F))
    return out


def put_uint_le(buffer: np.ndarray, offset: int, value: int, width: int) -> None:
    raw = int(value).to_bytes(width, byteorder="little", signed=False)
    buffer[offset : offset + width] = np.frombuffer(raw, dtype=np.uint8)


def put_int16_le(buffer: np.ndarray, offset: int, value: int) -> None:
    raw = int(value).to_bytes(2, byteorder="little", signed=True)
    buffer[offset : offset + 2] = np.frombuffer(raw, dtype=np.uint8)


def log_time_bucket(duration_us: int, *, scale: int = 32, bits: int = 10) -> int:
    value = int(round(math.log2(1 + max(0, int(duration_us))) * scale))
    return max(0, min((1 << bits) - 1, value))


def size_bucket(size: float, *, scale: int = 16) -> np.uint8:
    value = int(round(math.log2(1.0 + max(0.0, float(size)) / 100.0) * scale))
    return np.uint8(max(0, min(255, value)))


def correction_code(value: Any) -> int:
    try:
        code = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 15
    if 0 <= code <= 14:
        return code
    return 15


def as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0
