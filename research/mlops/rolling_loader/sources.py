from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient
from research.mlops.rolling_loader.cache import ExternalContextPayload
from research.mlops.rolling_loader.synthetic import SyntheticEvent


@dataclass(frozen=True, slots=True)
class ClickHouseReplayConfig:
    database: str = "market_sip_compact"
    events_table: str = "events"
    index_table: str = "train_2019_to_2025"
    date: str = ""
    max_threads: int = 8
    max_memory_usage: str = "80G"


@dataclass(frozen=True, slots=True)
class RollingTickerIndexRow:
    ticker: str
    first_ordinal: int
    max_valid_ordinal: int
    split_event_count: int


@dataclass(frozen=True, slots=True)
class RollingEventBlock:
    """Rows fetched from a vectorized per-ticker ordinal cursor request."""

    tickers: tuple[str, ...]
    rows: np.ndarray
    ticker_index: np.ndarray

    @property
    def row_count(self) -> int:
        return int(self.rows.shape[0])

    @property
    def min_timestamp_us(self) -> int | None:
        if self.rows.size == 0:
            return None
        return int(np.min(self.rows["sip_timestamp_us"]))

    @property
    def max_timestamp_us(self) -> int | None:
        if self.rows.size == 0:
            return None
        return int(np.max(self.rows["sip_timestamp_us"]))

    def iter_chronological(self) -> Iterable[SyntheticEvent]:
        if self.rows.size == 0:
            return
        order = np.lexsort((self.ticker_index, self.rows["ordinal"], self.rows["sip_timestamp_us"]))
        for row_index in order:
            ticker = self.tickers[int(self.ticker_index[int(row_index)])]
            yield SyntheticEvent(ticker=ticker, row=self.rows[int(row_index)])

    def latest_ordinals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ticker_index, ticker in enumerate(self.tickers):
            mask = self.ticker_index == int(ticker_index)
            if np.any(mask):
                out[ticker] = int(np.max(self.rows["ordinal"][mask]))
        return out


@dataclass(frozen=True, slots=True)
class LowFrequencyContextUpdate:
    kind: str
    ticker: str
    timestamp_us: int
    payload: ExternalContextPayload
    global_item: bool = False


class ClickHouseRollingSource:
    """Minimal historical source that can feed the stateful loader.

    External contexts are intentionally not materialized here. They should be
    pushed through the same ``RollingContextLoader.push_external`` API by a
    context source, keeping event replay and low-frequency context replay
    independently profiled.
    """

    def __init__(
        self,
        *,
        config: ClickHouseReplayConfig,
        text_client: ClickHouseHttpClient,
        bytes_client: PersistentClickHouseBytesClient,
    ) -> None:
        self.config = config
        self.text_client = text_client
        self.bytes_client = bytes_client

    def close(self) -> None:
        self.bytes_client.close()

    def load_ticker_index_rows(self, *, limit: int = 0, min_events: int = 1) -> list[RollingTickerIndexRow]:
        limit_sql = f" LIMIT {int(limit)}" if int(limit) > 0 else ""
        query = f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal,
    split_event_count
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.index_table)}
WHERE split_event_count >= {int(min_events)}
  AND max_valid_ordinal >= first_ordinal + {int(min_events) - 1}
ORDER BY ticker
{limit_sql}
FORMAT TSV
"""
        rows: list[RollingTickerIndexRow] = []
        for line in self.text_client.execute(query).splitlines():
            if not line.strip():
                continue
            ticker, first_ordinal, max_valid_ordinal, split_event_count = line.split("\t")
            rows.append(
                RollingTickerIndexRow(
                    ticker=ticker.upper(),
                    first_ordinal=int(first_ordinal),
                    max_valid_ordinal=int(max_valid_ordinal),
                    split_event_count=int(split_event_count),
                )
            )
        return rows

    def load_tickers_from_index(self, *, limit: int = 0) -> tuple[str, ...]:
        return tuple(row.ticker for row in self.load_ticker_index_rows(limit=limit, min_events=1))

    def warm_rows_from_index(self, *, index_rows: Iterable[RollingTickerIndexRow], warm_count: int) -> dict[str, np.ndarray]:
        row_tuple = tuple(index_rows)
        if not row_tuple:
            return {}
        ticker_tuple = tuple(row.ticker for row in row_tuple)
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        first_sql = "[" + ", ".join(str(int(row.first_ordinal)) for row in row_tuple) + "]"
        end_sql = "[" + ", ".join(str(min(int(row.max_valid_ordinal), int(row.first_ordinal) + int(warm_count) - 1)) for row in row_tuple) + "]"
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    {first_sql} AS request_first_ordinals,
    {end_sql} AS request_end_ordinals
SELECT
    toUInt32(indexOf(request_tickers, ticker) - 1) AS span_id,
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
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
WHERE ticker IN request_tickers
  AND ordinal >= arrayElement(request_first_ordinals, indexOf(request_tickers, ticker))
  AND ordinal <= arrayElement(request_end_ordinals, indexOf(request_tickers, ticker))
ORDER BY ticker, ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT RowBinary
"""
        payload = self.bytes_client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()
        out: dict[str, np.ndarray] = {}
        if rows.size == 0:
            return out
        span_ids = rows["span_id"]
        boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [rows.shape[0]]))
        for start, end in zip(starts, ends):
            out[ticker_tuple[int(span_ids[start])]] = rows[start:end].copy()
        return out

    @staticmethod
    def initial_cursors_from_index(*, index_rows: Iterable[RollingTickerIndexRow], warm_count: int) -> dict[str, int]:
        cursors: dict[str, int] = {}
        for row in index_rows:
            warm_end = min(int(row.max_valid_ordinal), int(row.first_ordinal) + int(warm_count) - 1)
            cursors[row.ticker] = int(warm_end)
        return cursors

    def fetch_day_rows_by_ticker(self, *, tickers: Iterable[str], date: str) -> dict[str, np.ndarray]:
        ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
        if not ticker_tuple:
            return {}
        ticker_sql = ", ".join(sql_string(ticker) for ticker in ticker_tuple)
        query = f"""
SELECT
    toUInt32(arrayEnumerate([{ticker_sql}])[indexOf([{ticker_sql}], ticker)] - 1) AS span_id,
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
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
WHERE ticker IN ({ticker_sql})
  AND event_date = toDate({sql_string(date)})
ORDER BY ticker, ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT RowBinary
"""
        payload = self.bytes_client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE)
        out: dict[str, np.ndarray] = {}
        if rows.size == 0:
            return out
        span_ids = rows["span_id"]
        boundaries = np.flatnonzero(span_ids[1:] != span_ids[:-1]) + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [rows.shape[0]]))
        for start, end in zip(starts, ends):
            out[ticker_tuple[int(span_ids[start])]] = rows[start:end].copy()
        return out

    def fetch_next_by_ordinal(self, *, cursors: dict[str, int], rows_per_ticker: int) -> RollingEventBlock:
        ticker_tuple = tuple(str(ticker).upper() for ticker in cursors if str(ticker).strip())
        if not ticker_tuple:
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        cursor_sql = "[" + ", ".join(str(int(cursors[ticker])) for ticker in ticker_tuple) + "]"
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    {cursor_sql} AS request_cursors
SELECT
    toUInt32(indexOf(request_tickers, ticker) - 1) AS span_id,
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
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
WHERE ticker IN request_tickers
  AND ordinal > arrayElement(request_cursors, indexOf(request_tickers, ticker))
  AND ordinal <= arrayElement(request_cursors, indexOf(request_tickers, ticker)) + {int(rows_per_ticker)}
ORDER BY sip_timestamp_us, ticker, ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT RowBinary
"""
        payload = self.bytes_client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()
        ticker_index = rows["span_id"].astype(np.uint32, copy=True) if rows.size else np.zeros((0,), dtype=np.uint32)
        return RollingEventBlock(tickers=ticker_tuple, rows=rows, ticker_index=ticker_index)

    @staticmethod
    def _memory_bytes(value: str) -> int:
        text = str(value).strip().upper()
        multiplier = 1
        if text.endswith("G"):
            multiplier = 1024**3
            text = text[:-1]
        elif text.endswith("M"):
            multiplier = 1024**2
            text = text[:-1]
        elif text.endswith("K"):
            multiplier = 1024
            text = text[:-1]
        return int(float(text) * multiplier)


def iter_rows_by_ticker_chronological(rows_by_ticker: dict[str, np.ndarray]) -> Iterable[SyntheticEvent]:
    positions = {ticker: 0 for ticker in rows_by_ticker}
    while True:
        best_ticker = ""
        best_ts: int | None = None
        for ticker, rows in rows_by_ticker.items():
            pos = positions[ticker]
            if pos >= rows.shape[0]:
                continue
            ts = int(rows[pos]["sip_timestamp_us"])
            if best_ts is None or ts < best_ts:
                best_ts = ts
                best_ticker = ticker
        if best_ts is None:
            break
        pos = positions[best_ticker]
        positions[best_ticker] = pos + 1
        yield SyntheticEvent(ticker=best_ticker, row=rows_by_ticker[best_ticker][pos])


class SyntheticOrdinalBlockSource:
    """Vectorized per-ticker ordinal cursor source for profiler runs."""

    def __init__(self, rows_by_ticker: dict[str, np.ndarray]) -> None:
        self.rows_by_ticker = {str(ticker).upper(): rows for ticker, rows in rows_by_ticker.items()}

    def warm_rows(self, *, count: int) -> dict[str, np.ndarray]:
        return {ticker: rows[: int(count)].copy() for ticker, rows in self.rows_by_ticker.items()}

    def initial_cursors(self, *, warm_count: int) -> dict[str, int]:
        cursors: dict[str, int] = {}
        for ticker, rows in self.rows_by_ticker.items():
            take = min(int(warm_count), int(rows.shape[0]))
            cursors[ticker] = int(rows[take - 1]["ordinal"]) if take > 0 else -1
        return cursors

    def fetch_next_by_ordinal(self, *, cursors: dict[str, int], rows_per_ticker: int) -> RollingEventBlock:
        block_rows: list[np.ndarray] = []
        block_ticker_index: list[np.ndarray] = []
        tickers = tuple(cursors.keys())
        for ticker_index, ticker in enumerate(tickers):
            rows = self.rows_by_ticker.get(ticker)
            if rows is None or rows.size == 0:
                continue
            cursor = int(cursors[ticker])
            first = cursor + 1
            if first >= rows.shape[0]:
                continue
            end = min(first + int(rows_per_ticker), rows.shape[0])
            part = rows[first:end].copy()
            part["span_id"] = int(ticker_index)
            block_rows.append(part)
            block_ticker_index.append(np.full((part.shape[0],), int(ticker_index), dtype=np.uint32))
        if not block_rows:
            return RollingEventBlock(tickers=tickers, rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        rows = np.concatenate(block_rows)
        ticker_index = np.concatenate(block_ticker_index)
        order = np.lexsort((ticker_index, rows["ordinal"], rows["sip_timestamp_us"]))
        return RollingEventBlock(tickers=tickers, rows=rows[order], ticker_index=ticker_index[order])
