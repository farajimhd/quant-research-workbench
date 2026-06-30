from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient
from research.mlops.rolling_loader.cache import ExternalContextPayload


DEFAULT_TICKER_QUERY_CHUNK_SIZE = 512
NEXT_EVENT_LOOKAHEAD_DAYS = 7
MAX_NEXT_EVENT_SEARCH_DAYS = 3650
WARM_ROW_LOOKBACK_DAYS = 30


@dataclass(frozen=True, slots=True)
class ClickHouseReplayConfig:
    database: str = "market_sip_compact"
    events_table: str = "events"
    index_table: str = "train_2019_to_2025"
    date: str = ""
    max_threads: int = 8
    max_memory_usage: str = "80G"


@dataclass(frozen=True, slots=True)
class ClickHouseExternalContextConfig:
    database: str = "market_sip_compact"
    sec_context_database: str = "market_sip_compact"
    news_token_table: str = "news_text_tokens"
    sec_filing_text_token_table: str = "sec_filing_text_tokens"
    sec_xbrl_context_table: str = "sec_xbrl_context"
    macro_bars_table: str = "macro_bars_by_time_symbol"
    news_lookback_days: int = 30
    sec_lookback_days: int = 365
    xbrl_lookback_days: int = 730
    macro_lookback_days: int = 400
    ticker_news_items: int = 32
    global_news_items: int = 64
    sec_filing_items: int = 16
    xbrl_items: int = 512
    news_token_chunks: int = 2
    sec_token_chunks: int = 8
    text_max_tokens: int = 1024
    macro_timeframes: tuple[str, ...] = ("1d", "1w", "1mo", "1y")
    global_symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")


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

    def iter_chronological(self) -> Iterable["ReplayEvent"]:
        if self.rows.size == 0:
            return
        order = np.lexsort((self.ticker_index, self.rows["ordinal"], self.rows["sip_timestamp_us"]))
        for row_index in order:
            ticker = self.tickers[int(self.ticker_index[int(row_index)])]
            yield ReplayEvent(ticker=ticker, row=self.rows[int(row_index)])

    def latest_ordinals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ticker_index, ticker in enumerate(self.tickers):
            mask = self.ticker_index == int(ticker_index)
            if np.any(mask):
                out[ticker] = int(np.max(self.rows["ordinal"][mask]))
        return out

    def first_ordinals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ticker_index, ticker in enumerate(self.tickers):
            mask = self.ticker_index == int(ticker_index)
            if np.any(mask):
                out[ticker] = int(np.min(self.rows["ordinal"][mask]))
        return out


@dataclass(frozen=True, slots=True)
class LowFrequencyContextUpdate:
    kind: str
    ticker: str
    timestamp_us: int
    payload: ExternalContextPayload
    global_item: bool = False


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    ticker: str
    row: np.void


@dataclass(frozen=True, slots=True)
class TimestampedReplayItem:
    timestamp_us: int
    event: ReplayEvent | None = None
    context: LowFrequencyContextUpdate | None = None


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

    def load_ticker_index_rows_for_tickers(
        self,
        *,
        tickers: Iterable[str],
        min_events: int = 1,
    ) -> list[RollingTickerIndexRow]:
        ticker_tuple = tuple(sorted({str(ticker).upper() for ticker in tickers if str(ticker).strip()}))
        if not ticker_tuple:
            return []
        ticker_sql = ", ".join(sql_string(ticker) for ticker in ticker_tuple)
        query = f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal,
    split_event_count
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.index_table)}
WHERE ticker IN ({ticker_sql})
  AND split_event_count >= {int(min_events)}
  AND max_valid_ordinal >= first_ordinal + {int(min_events) - 1}
ORDER BY ticker
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
    condition_tokens_packed
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

    def load_start_ordinals(
        self,
        *,
        index_rows: Iterable[RollingTickerIndexRow],
        start_timestamp_us: int,
        chunk_size: int = DEFAULT_TICKER_QUERY_CHUNK_SIZE,
    ) -> dict[str, int]:
        """Resolve each ticker's replay cursor as-of the requested timestamp."""

        row_tuple = tuple(index_rows)
        if not row_tuple:
            return {}
        out: dict[str, int] = {}
        for chunk in _chunks(row_tuple, max(1, int(chunk_size))):
            out.update(self._load_start_ordinals_chunk(index_rows=chunk, start_timestamp_us=int(start_timestamp_us)))
        return out

    def _load_start_ordinals_chunk(
        self,
        *,
        index_rows: tuple[RollingTickerIndexRow, ...],
        start_timestamp_us: int,
    ) -> dict[str, int]:
        row_tuple = tuple(index_rows)
        if not row_tuple:
            return {}
        ticker_tuple = tuple(row.ticker for row in row_tuple)
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        first_sql = "[" + ", ".join(str(int(row.first_ordinal)) for row in row_tuple) + "]"
        max_sql = "[" + ", ".join(str(int(row.max_valid_ordinal)) for row in row_tuple) + "]"
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_timestamp_us)))})"
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    {first_sql} AS request_first_ordinals,
    {max_sql} AS request_max_ordinals
SELECT
    ticker,
    max(ordinal) AS start_ordinal
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
PREWHERE ticker IN request_tickers
  AND event_date <= {start_date_sql}
WHERE sip_timestamp_us <= {int(start_timestamp_us)}
  AND ordinal >= arrayElement(request_first_ordinals, indexOf(request_tickers, ticker))
  AND ordinal <= arrayElement(request_max_ordinals, indexOf(request_tickers, ticker))
GROUP BY ticker
ORDER BY ticker
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        out: dict[str, int] = {}
        for line in self.text_client.execute(query).splitlines():
            if not line.strip():
                continue
            ticker, ordinal = line.split("\t")
            out[str(ticker).upper()] = int(ordinal)
        return out

    def warm_rows_ending_at(
        self,
        *,
        index_rows: Iterable[RollingTickerIndexRow],
        end_ordinals: Mapping[str, int],
        warm_count: int,
        chunk_size: int = DEFAULT_TICKER_QUERY_CHUNK_SIZE,
        asof_timestamp_us: int = 0,
        lookback_days: int = WARM_ROW_LOOKBACK_DAYS,
    ) -> dict[str, np.ndarray]:
        """Load the in-memory high-frequency carryover ending at each cursor."""

        by_ticker = {row.ticker: row for row in index_rows}
        ticker_tuple = tuple(ticker for ticker in sorted(end_ordinals) if ticker in by_ticker)
        if not ticker_tuple:
            return {}
        out: dict[str, np.ndarray] = {}
        for ticker_chunk in _chunks(ticker_tuple, max(1, int(chunk_size))):
            out.update(
                self._warm_rows_ending_at_chunk(
                    ticker_tuple=ticker_chunk,
                    by_ticker=by_ticker,
                    end_ordinals=end_ordinals,
                    warm_count=int(warm_count),
                    asof_timestamp_us=int(asof_timestamp_us),
                    lookback_days=int(lookback_days),
                )
            )
        if int(asof_timestamp_us) > 0:
            deficient = tuple(
                ticker
                for ticker in ticker_tuple
                if int(out.get(ticker, np.empty(0, dtype=EVENT_ROW_DTYPE)).size)
                < self._warm_expected_count(
                    row=by_ticker[ticker],
                    end_ordinal=int(end_ordinals[ticker]),
                    warm_count=int(warm_count),
                )
            )
            for ticker_chunk in _chunks(deficient, max(1, int(chunk_size))):
                out.update(
                    self._warm_rows_ending_at_chunk(
                        ticker_tuple=ticker_chunk,
                        by_ticker=by_ticker,
                        end_ordinals=end_ordinals,
                        warm_count=int(warm_count),
                    )
                )
        return out

    @staticmethod
    def _warm_expected_count(*, row: RollingTickerIndexRow, end_ordinal: int, warm_count: int) -> int:
        end = min(int(end_ordinal), int(row.max_valid_ordinal))
        start = max(int(row.first_ordinal), end - int(warm_count) + 1)
        return max(0, end - start + 1)

    def _warm_rows_ending_at_chunk(
        self,
        *,
        ticker_tuple: tuple[str, ...],
        by_ticker: Mapping[str, RollingTickerIndexRow],
        end_ordinals: Mapping[str, int],
        warm_count: int,
        asof_timestamp_us: int = 0,
        lookback_days: int = WARM_ROW_LOOKBACK_DAYS,
    ) -> dict[str, np.ndarray]:
        starts = []
        ends = []
        for ticker in ticker_tuple:
            row = by_ticker[ticker]
            end_ordinal = min(int(end_ordinals[ticker]), int(row.max_valid_ordinal))
            start_ordinal = max(int(row.first_ordinal), end_ordinal - int(warm_count) + 1)
            starts.append(start_ordinal)
            ends.append(end_ordinal)
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        start_sql = "[" + ", ".join(str(value) for value in starts) + "]"
        end_sql = "[" + ", ".join(str(value) for value in ends) + "]"
        if int(asof_timestamp_us) > 0:
            end_date = _date_obj_from_us(int(asof_timestamp_us))
            start_date = end_date - dt.timedelta(days=max(0, int(lookback_days)))
            prewhere_sql = f"""
PREWHERE ticker IN request_tickers
  AND event_date >= toDate({sql_string(start_date.isoformat())})
  AND event_date <= toDate({sql_string(end_date.isoformat())})
WHERE ordinal >= arrayElement(request_start_ordinals, indexOf(request_tickers, ticker))
  AND ordinal <= arrayElement(request_end_ordinals, indexOf(request_tickers, ticker))
"""
        else:
            prewhere_sql = """
WHERE ticker IN request_tickers
  AND ordinal >= arrayElement(request_start_ordinals, indexOf(request_tickers, ticker))
  AND ordinal <= arrayElement(request_end_ordinals, indexOf(request_tickers, ticker))
"""
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    {start_sql} AS request_start_ordinals,
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
    condition_tokens_packed
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
{prewhere_sql}
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
        starts_array = np.concatenate(([0], boundaries))
        ends_array = np.concatenate((boundaries, [rows.shape[0]]))
        for start, end in zip(starts_array, ends_array):
            out[ticker_tuple[int(span_ids[start])]] = rows[start:end].copy()
        return out

    @staticmethod
    def initial_cursors_from_index(*, index_rows: Iterable[RollingTickerIndexRow], warm_count: int) -> dict[str, int]:
        cursors: dict[str, int] = {}
        for row in index_rows:
            warm_end = min(int(row.max_valid_ordinal), int(row.first_ordinal) + int(warm_count) - 1)
            cursors[row.ticker] = int(warm_end)
        return cursors

    @staticmethod
    def initial_cursors_from_ordinals(*, end_ordinals: Mapping[str, int]) -> dict[str, int]:
        return {str(ticker).upper(): int(ordinal) for ticker, ordinal in end_ordinals.items()}

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
    condition_tokens_packed
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
    condition_tokens_packed
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

    def fetch_time_window(
        self,
        *,
        tickers: Iterable[str],
        start_exclusive_us: int,
        end_inclusive_us: int,
        chunk_size: int = DEFAULT_TICKER_QUERY_CHUNK_SIZE,
    ) -> RollingEventBlock:
        ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
        if not ticker_tuple or int(end_inclusive_us) <= int(start_exclusive_us):
            return RollingEventBlock(tickers=ticker_tuple, rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        block_rows: list[np.ndarray] = []
        block_ticker_index: list[np.ndarray] = []
        global_index = {ticker: index for index, ticker in enumerate(ticker_tuple)}
        for ticker_chunk in _chunks(ticker_tuple, max(1, int(chunk_size))):
            block = self._fetch_time_window_chunk(
                ticker_tuple=ticker_chunk,
                start_exclusive_us=int(start_exclusive_us),
                end_inclusive_us=int(end_inclusive_us),
            )
            if block.row_count <= 0:
                continue
            rows = block.rows.copy()
            remapped = np.asarray([global_index[block.tickers[int(local_index)]] for local_index in block.ticker_index], dtype=np.uint32)
            rows["span_id"] = remapped
            block_rows.append(rows)
            block_ticker_index.append(remapped)
        if not block_rows:
            return RollingEventBlock(tickers=ticker_tuple, rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        rows = np.concatenate(block_rows)
        ticker_index = np.concatenate(block_ticker_index)
        order = np.lexsort((ticker_index, rows["ordinal"], rows["sip_timestamp_us"]))
        return RollingEventBlock(tickers=ticker_tuple, rows=rows[order], ticker_index=ticker_index[order])

    def _fetch_time_window_chunk(
        self,
        *,
        ticker_tuple: tuple[str, ...],
        start_exclusive_us: int,
        end_inclusive_us: int,
    ) -> RollingEventBlock:
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_exclusive_us)))})"
        end_date_sql = f"toDate({sql_string(_date_from_us(int(end_inclusive_us)))})"
        query = f"""
WITH
    {ticker_sql} AS request_tickers
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
    condition_tokens_packed
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
PREWHERE ticker IN request_tickers
  AND event_date >= {start_date_sql}
  AND event_date <= {end_date_sql}
WHERE sip_timestamp_us > {int(start_exclusive_us)}
  AND sip_timestamp_us <= {int(end_inclusive_us)}
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

    def fetch_next_time_window(
        self,
        *,
        tickers: Iterable[str],
        start_exclusive_us: int,
        window_us: int,
        chunk_size: int = DEFAULT_TICKER_QUERY_CHUNK_SIZE,
    ) -> RollingEventBlock:
        """Fetch the next non-empty event-anchored time window after the cursor."""

        ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
        if not ticker_tuple or int(window_us) <= 0:
            return RollingEventBlock(tickers=ticker_tuple, rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        next_start_us = self._next_event_timestamp_us(
            tickers=ticker_tuple,
            start_exclusive_us=int(start_exclusive_us),
            chunk_size=max(1, int(chunk_size)),
        )
        if next_start_us is None:
            return RollingEventBlock(tickers=ticker_tuple, rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        return self.fetch_time_window(
            tickers=ticker_tuple,
            start_exclusive_us=int(next_start_us) - 1,
            end_inclusive_us=int(next_start_us) + int(window_us),
            chunk_size=max(1, int(chunk_size)),
        )

    def _next_event_timestamp_us(
        self,
        *,
        tickers: tuple[str, ...],
        start_exclusive_us: int,
        chunk_size: int,
    ) -> int | None:
        next_values: list[int] = []
        for ticker_chunk in _chunks(tickers, max(1, int(chunk_size))):
            value = self._next_event_timestamp_us_chunk(ticker_tuple=ticker_chunk, start_exclusive_us=int(start_exclusive_us))
            if value is not None:
                next_values.append(int(value))
        return min(next_values) if next_values else None

    def _next_event_timestamp_us_chunk(
        self,
        *,
        ticker_tuple: tuple[str, ...],
        start_exclusive_us: int,
    ) -> int | None:
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in ticker_tuple) + "]"
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_exclusive_us)))})"
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    toUInt64({int(start_exclusive_us)}) AS request_start_us
SELECT
    minOrNull(sip_timestamp_us) AS next_start_us
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
PREWHERE ticker IN request_tickers
  AND event_date >= {start_date_sql}
WHERE sip_timestamp_us > request_start_us
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        text = self.text_client.execute(query).strip()
        if not text or text == "\\N":
            return None
        return int(text.splitlines()[0])

    def fetch_next_time_window_from_index(
        self,
        *,
        start_exclusive_us: int,
        window_us: int,
        min_events: int,
        ticker_limit: int = 0,
    ) -> RollingEventBlock:
        """Fetch the next non-empty event window without sending a ticker array."""

        if int(window_us) <= 0:
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        if int(ticker_limit) > 0:
            next_start_us = self._next_event_timestamp_from_index(
                start_exclusive_us=int(start_exclusive_us),
                min_events=int(min_events),
                ticker_limit=int(ticker_limit),
            )
        else:
            next_start_us = self._next_event_timestamp_all(start_exclusive_us=int(start_exclusive_us))
        if next_start_us is None:
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        end_inclusive_us = int(next_start_us) + int(window_us)
        if int(ticker_limit) > 0:
            tickers = self._window_tickers_from_index(
                start_exclusive_us=int(next_start_us) - 1,
                end_inclusive_us=end_inclusive_us,
                min_events=int(min_events),
                ticker_limit=int(ticker_limit),
            )
            if not tickers:
                return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
            return self.fetch_time_window(
                tickers=tickers,
                start_exclusive_us=int(next_start_us) - 1,
                end_inclusive_us=end_inclusive_us,
            )
        return self.fetch_time_window_all(
            start_exclusive_us=int(next_start_us) - 1,
            end_inclusive_us=end_inclusive_us,
        )

    def _next_event_timestamp_all(self, *, start_exclusive_us: int) -> int | None:
        start_date = _date_obj_from_us(int(start_exclusive_us))
        for offset_days in range(0, MAX_NEXT_EVENT_SEARCH_DAYS, NEXT_EVENT_LOOKAHEAD_DAYS):
            window_start = start_date + dt.timedelta(days=offset_days)
            window_end = window_start + dt.timedelta(days=NEXT_EVENT_LOOKAHEAD_DAYS)
            value = self._next_event_timestamp_all_date_range(
                start_exclusive_us=int(start_exclusive_us),
                start_date=window_start,
                end_date=window_end,
            )
            if value is not None:
                return value
        return None

    def _next_event_timestamp_all_date_range(
        self,
        *,
        start_exclusive_us: int,
        start_date: dt.date,
        end_date: dt.date,
    ) -> int | None:
        start_date_sql = f"toDate({sql_string(start_date.isoformat())})"
        end_date_sql = f"toDate({sql_string(end_date.isoformat())})"
        query = f"""
WITH toUInt64({int(start_exclusive_us)}) AS request_start_us
SELECT minOrNull(sip_timestamp_us) AS next_start_us
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
PREWHERE event_date >= {start_date_sql}
  AND event_date < {end_date_sql}
WHERE sip_timestamp_us > request_start_us
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        text = self.text_client.execute(query).strip()
        if not text or text == "\\N":
            return None
        return int(text.splitlines()[0])

    def fetch_time_window_all(self, *, start_exclusive_us: int, end_inclusive_us: int) -> RollingEventBlock:
        if int(end_inclusive_us) <= int(start_exclusive_us):
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_exclusive_us)))})"
        end_date_sql = f"toDate({sql_string(_date_from_us(int(end_inclusive_us)))})"
        query = f"""
WITH window_rows AS
(
    SELECT
        ticker,
        ordinal,
        event_type,
        sip_timestamp_us,
        price_primary_int,
        price_secondary_int,
        size_primary,
        size_secondary,
        exchange_primary,
        exchange_secondary,
        condition_tokens_packed
    FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
    PREWHERE event_date >= {start_date_sql}
      AND event_date <= {end_date_sql}
    WHERE sip_timestamp_us > {int(start_exclusive_us)}
      AND sip_timestamp_us <= {int(end_inclusive_us)}
),
window_tickers AS
(
    SELECT groupArray(ticker) AS request_tickers
    FROM
    (
        SELECT DISTINCT ticker
        FROM window_rows
        ORDER BY ticker
    )
)
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
    condition_tokens_packed
FROM window_rows
CROSS JOIN window_tickers
ORDER BY sip_timestamp_us, ticker, ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT RowBinary
"""
        payload = self.bytes_client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()
        if rows.size == 0:
            return RollingEventBlock(tickers=(), rows=rows, ticker_index=np.zeros((0,), dtype=np.uint32))
        tickers = self._window_tickers_all(start_exclusive_us=int(start_exclusive_us), end_inclusive_us=int(end_inclusive_us))
        ticker_index = rows["span_id"].astype(np.uint32, copy=True)
        return RollingEventBlock(tickers=tickers, rows=rows, ticker_index=ticker_index)

    def _window_tickers_all(self, *, start_exclusive_us: int, end_inclusive_us: int) -> tuple[str, ...]:
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_exclusive_us)))})"
        end_date_sql = f"toDate({sql_string(_date_from_us(int(end_inclusive_us)))})"
        query = f"""
SELECT DISTINCT ticker
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
PREWHERE event_date >= {start_date_sql}
  AND event_date <= {end_date_sql}
WHERE sip_timestamp_us > {int(start_exclusive_us)}
  AND sip_timestamp_us <= {int(end_inclusive_us)}
ORDER BY ticker
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        return tuple(str(line).upper() for line in self.text_client.execute(query).splitlines() if line.strip())

    def fetch_event_stream_all(self, *, start_exclusive_us: int, max_rows: int) -> RollingEventBlock:
        if int(max_rows) <= 0:
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        start_date = _date_obj_from_us(int(start_exclusive_us))
        for offset_days in range(0, MAX_NEXT_EVENT_SEARCH_DAYS, NEXT_EVENT_LOOKAHEAD_DAYS):
            window_start = start_date + dt.timedelta(days=offset_days)
            window_end = window_start + dt.timedelta(days=NEXT_EVENT_LOOKAHEAD_DAYS)
            block = self._fetch_event_stream_all_date_range(
                start_exclusive_us=int(start_exclusive_us),
                max_rows=int(max_rows),
                start_date=window_start,
                end_date=window_end,
            )
            if block.row_count > 0:
                return block
        return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))

    def _fetch_event_stream_all_date_range(
        self,
        *,
        start_exclusive_us: int,
        max_rows: int,
        start_date: dt.date,
        end_date: dt.date,
    ) -> RollingEventBlock:
        start_date_sql = f"toDate({sql_string(start_date.isoformat())})"
        end_date_sql = f"toDate({sql_string(end_date.isoformat())})"
        stream_cte = f"""
stream_rows AS
(
    SELECT
        ticker,
        ordinal,
        event_type,
        sip_timestamp_us,
        price_primary_int,
        price_secondary_int,
        size_primary,
        size_secondary,
        exchange_primary,
        exchange_secondary,
        condition_tokens_packed
    FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}
    PREWHERE event_date >= {start_date_sql}
      AND event_date < {end_date_sql}
    WHERE sip_timestamp_us > {int(start_exclusive_us)}
    ORDER BY sip_timestamp_us, ticker, ordinal
    LIMIT {int(max_rows)}
)
"""
        ticker_query = f"""
WITH {stream_cte}
SELECT DISTINCT ticker
FROM stream_rows
ORDER BY ticker
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        tickers = tuple(str(line).upper() for line in self.text_client.execute(ticker_query).splitlines() if line.strip())
        if not tickers:
            return RollingEventBlock(tickers=(), rows=np.zeros((0,), dtype=EVENT_ROW_DTYPE), ticker_index=np.zeros((0,), dtype=np.uint32))
        ticker_sql = "[" + ", ".join(sql_string(ticker) for ticker in tickers) + "]"
        query = f"""
WITH
    {ticker_sql} AS request_tickers,
    {stream_cte}
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
    condition_tokens_packed
FROM stream_rows
ORDER BY sip_timestamp_us, ticker, ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT RowBinary
"""
        payload = self.bytes_client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by event row size {EVENT_ROW_DTYPE.itemsize}")
        rows = np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()
        ticker_index = rows["span_id"].astype(np.uint32, copy=True) if rows.size else np.zeros((0,), dtype=np.uint32)
        return RollingEventBlock(tickers=tickers, rows=rows, ticker_index=ticker_index)

    def _next_event_timestamp_from_index(
        self,
        *,
        start_exclusive_us: int,
        min_events: int,
        ticker_limit: int,
    ) -> int | None:
        start_date = _date_obj_from_us(int(start_exclusive_us))
        for offset_days in range(0, MAX_NEXT_EVENT_SEARCH_DAYS, NEXT_EVENT_LOOKAHEAD_DAYS):
            window_start = start_date + dt.timedelta(days=offset_days)
            window_end = window_start + dt.timedelta(days=NEXT_EVENT_LOOKAHEAD_DAYS)
            value = self._next_event_timestamp_from_index_date_range(
                start_exclusive_us=int(start_exclusive_us),
                min_events=int(min_events),
                ticker_limit=int(ticker_limit),
                start_date=window_start,
                end_date=window_end,
            )
            if value is not None:
                return value
        return None

    def _next_event_timestamp_from_index_date_range(
        self,
        *,
        start_exclusive_us: int,
        min_events: int,
        ticker_limit: int,
        start_date: dt.date,
        end_date: dt.date,
    ) -> int | None:
        start_date_sql = f"toDate({sql_string(start_date.isoformat())})"
        end_date_sql = f"toDate({sql_string(end_date.isoformat())})"
        eligible_sql = self._eligible_index_subquery(min_events=int(min_events), ticker_limit=int(ticker_limit))
        query = f"""
WITH
    toUInt64({int(start_exclusive_us)}) AS request_start_us
SELECT minOrNull(e.sip_timestamp_us) AS next_start_us
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)} AS e
INNER JOIN ({eligible_sql}) AS i ON e.ticker = i.ticker
WHERE e.event_date >= {start_date_sql}
  AND e.event_date < {end_date_sql}
  AND e.sip_timestamp_us > request_start_us
  AND e.ordinal >= i.first_ordinal
  AND e.ordinal <= i.max_valid_ordinal
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        text = self.text_client.execute(query).strip()
        if not text or text == "\\N":
            return None
        return int(text.splitlines()[0])

    def _window_tickers_from_index(
        self,
        *,
        start_exclusive_us: int,
        end_inclusive_us: int,
        min_events: int,
        ticker_limit: int,
    ) -> tuple[str, ...]:
        start_date_sql = f"toDate({sql_string(_date_from_us(int(start_exclusive_us)))})"
        end_date_sql = f"toDate({sql_string(_date_from_us(int(end_inclusive_us)))})"
        eligible_sql = self._eligible_index_subquery(min_events=int(min_events), ticker_limit=int(ticker_limit))
        query = f"""
SELECT DISTINCT e.ticker
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.events_table)} AS e
INNER JOIN ({eligible_sql}) AS i ON e.ticker = i.ticker
WHERE e.event_date >= {start_date_sql}
  AND e.event_date <= {end_date_sql}
  AND e.sip_timestamp_us > {int(start_exclusive_us)}
  AND e.sip_timestamp_us <= {int(end_inclusive_us)}
  AND e.ordinal >= i.first_ordinal
  AND e.ordinal <= i.max_valid_ordinal
ORDER BY e.ticker
SETTINGS max_threads = {int(self.config.max_threads)}, max_memory_usage = {self._memory_bytes(self.config.max_memory_usage)}
FORMAT TSV
"""
        return tuple(str(line).upper() for line in self.text_client.execute(query).splitlines() if line.strip())

    def _eligible_index_subquery(self, *, min_events: int, ticker_limit: int) -> str:
        limit_sql = f" LIMIT {int(ticker_limit)}" if int(ticker_limit) > 0 else ""
        return f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.index_table)}
WHERE split_event_count >= {int(min_events)}
  AND max_valid_ordinal >= first_ordinal + {int(min_events) - 1}
ORDER BY ticker
{limit_sql}
"""

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


class ClickHouseExternalContextSource:
    """Fetches real low-frequency/global context rows for rolling replay.

    It returns cache updates only. The caller is responsible for merging those
    updates with event rows by timestamp before calling `push_external`.
    """

    def __init__(self, *, config: ClickHouseExternalContextConfig, text_client: ClickHouseHttpClient) -> None:
        self.config = config
        self.text_client = text_client
        self._seen_keys: set[tuple[Any, ...]] = set()
        self._fetched_until_us = 0

    def fetch_initial_and_block_updates(
        self,
        *,
        tickers: Iterable[str],
        start_timestamp_us: int,
        end_timestamp_us: int,
    ) -> list[LowFrequencyContextUpdate]:
        """Fetch updates needed before and during the next event block.

        The first call uses table-specific lookbacks so caches are warm before
        the first event in the block. Later calls fetch only newly available
        rows after the previous block high watermark.
        """

        if self._fetched_until_us <= 0:
            return self.load_initial_context_asof(tickers=tickers, asof_timestamp_us=int(end_timestamp_us))
        return self.fetch_context_updates(
            tickers=tickers,
            start_exclusive_us=max(int(start_timestamp_us) - 1, int(self._fetched_until_us)),
            end_inclusive_us=int(end_timestamp_us),
        )

    def load_initial_context_asof(
        self,
        *,
        tickers: Iterable[str],
        asof_timestamp_us: int,
    ) -> list[LowFrequencyContextUpdate]:
        """Load bounded low-frequency/global caches as-of replay start."""

        asof_us = int(asof_timestamp_us)
        if asof_us <= 0:
            return []
        lower_bounds = {
            "news": max(0, asof_us - int(self.config.news_lookback_days) * 86_400_000_000),
            "sec": max(0, asof_us - int(self.config.sec_lookback_days) * 86_400_000_000),
            "xbrl": max(0, asof_us - int(self.config.xbrl_lookback_days) * 86_400_000_000),
            "macro": max(0, asof_us - int(self.config.macro_lookback_days) * 86_400_000_000),
        }
        updates = self._fetch_context_range(tickers=tickers, lower_bounds=lower_bounds, end_us=asof_us)
        self._fetched_until_us = max(int(self._fetched_until_us), asof_us)
        return updates

    def fetch_context_updates(
        self,
        *,
        tickers: Iterable[str],
        start_exclusive_us: int,
        end_inclusive_us: int,
    ) -> list[LowFrequencyContextUpdate]:
        """Fetch only newly visible low-frequency/global rows for a replay block."""

        end_us = int(end_inclusive_us)
        if end_us <= int(start_exclusive_us) or end_us <= 0:
            return []
        start = max(int(start_exclusive_us), int(self._fetched_until_us)) + 1
        lower_bounds = {"news": start, "sec": start, "xbrl": start, "macro": start}
        updates = self._fetch_context_range(tickers=tickers, lower_bounds=lower_bounds, end_us=end_us)
        self._fetched_until_us = max(int(self._fetched_until_us), end_us)
        return updates

    def _fetch_context_range(
        self,
        *,
        tickers: Iterable[str],
        lower_bounds: Mapping[str, int],
        end_us: int,
    ) -> list[LowFrequencyContextUpdate]:
        ticker_tuple = tuple(sorted({str(ticker).upper() for ticker in tickers if str(ticker).strip()}))
        updates: list[LowFrequencyContextUpdate] = []
        updates.extend(self._fetch_global_news(start_us=lower_bounds["news"], end_us=end_us))
        for chunk_index, ticker_chunk in enumerate(_chunks(ticker_tuple, DEFAULT_TICKER_QUERY_CHUNK_SIZE)):
            updates.extend(self._fetch_ticker_news(tickers=ticker_chunk, start_us=lower_bounds["news"], end_us=end_us))
            updates.extend(self._fetch_sec_filings(tickers=ticker_chunk, start_us=lower_bounds["sec"], end_us=end_us))
            updates.extend(self._fetch_xbrl(tickers=ticker_chunk, start_us=lower_bounds["xbrl"], end_us=end_us))
            updates.extend(
                self._fetch_macro_bars(
                    tickers=ticker_chunk,
                    start_us=lower_bounds["macro"],
                    end_us=end_us,
                    include_global_symbols=chunk_index == 0,
                )
            )
        updates.sort(key=lambda item: (int(item.timestamp_us), str(item.kind), str(item.ticker)))
        return updates

    def _fetch_ticker_news(self, *, tickers: tuple[str, ...], start_us: int, end_us: int) -> list[LowFrequencyContextUpdate]:
        if not tickers:
            return []
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.news_token_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        row_limit = int(self.config.ticker_news_items) * int(self.config.news_token_chunks)
        start_expr = _date_time64_from_us(int(start_us))
        end_expr = _date_time64_from_us(int(end_us))
        query = f"""
SELECT *
FROM
(
    SELECT
        ticker,
        timestamp_us,
        source_id,
        provider,
        provider_article_id,
        title,
        article_url,
        url_domain,
        channels,
        provider_tags,
        quality_flags,
        tokenizer_model,
        max_tokens,
        token_chunk_index,
        input_ids,
        attention_mask,
        text_hash,
        text_char_count,
        source_text_char_count
    FROM {table}
    PREWHERE ticker IN ({ticker_sql})
    WHERE published_at_utc >= {start_expr}
      AND published_at_utc <= {end_expr}
      AND timestamp_us >= {int(start_us)}
      AND timestamp_us <= {int(end_us)}
    ORDER BY ticker, timestamp_us DESC, source_id, token_chunk_index
    LIMIT {row_limit} BY ticker
)
ORDER BY timestamp_us, ticker, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return self._token_updates(kind="ticker_news", rows=self._query_json_rows(query), chunks=int(self.config.news_token_chunks))

    def _fetch_global_news(self, *, start_us: int, end_us: int) -> list[LowFrequencyContextUpdate]:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.news_token_table)}"
        max_items = int(self.config.global_news_items)
        max_chunks = int(self.config.news_token_chunks)
        start_expr = _date_time64_from_us(int(start_us))
        end_expr = _date_time64_from_us(int(end_us))
        query = f"""
WITH latest_sources AS
(
    SELECT
        source_id,
        max(timestamp_us) AS latest_timestamp_us
    FROM {table}
    WHERE published_at_utc >= {start_expr}
      AND published_at_utc <= {end_expr}
      AND timestamp_us >= {int(start_us)}
      AND timestamp_us <= {int(end_us)}
    GROUP BY source_id
    ORDER BY latest_timestamp_us DESC
    LIMIT {max_items}
)
SELECT
    '__MARKET__' AS ticker,
    timestamp_us,
    source_id,
    provider,
    provider_article_id,
    title,
    article_url,
    url_domain,
    channels,
    provider_tags,
    quality_flags,
    tokenizer_model,
    max_tokens,
    token_chunk_index,
    input_ids,
    attention_mask,
    text_hash,
    text_char_count,
    source_text_char_count
FROM
(
    SELECT t.*
    FROM {table} AS t
    INNER JOIN latest_sources AS s ON t.source_id = s.source_id
    WHERE t.published_at_utc >= {start_expr}
      AND t.published_at_utc <= {end_expr}
      AND t.token_chunk_index < {max_chunks}
    ORDER BY t.source_id, t.token_chunk_index, t.ticker
    LIMIT 1 BY source_id, token_chunk_index
)
ORDER BY timestamp_us, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return self._token_updates(kind="global_news", rows=self._query_json_rows(query), chunks=max_chunks, global_item=True)

    def _fetch_sec_filings(self, *, tickers: tuple[str, ...], start_us: int, end_us: int) -> list[LowFrequencyContextUpdate]:
        if not tickers:
            return []
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.sec_filing_text_token_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        row_limit = int(self.config.sec_filing_items) * int(self.config.sec_token_chunks)
        start_expr = _date_time64_from_us(int(start_us))
        end_expr = _date_time64_from_us(int(end_us))
        query = f"""
SELECT *
FROM
(
    SELECT
        ticker,
        timestamp_us,
        source_id,
        accession_number,
        cik,
        form_type,
        text_rank,
        document_id,
        text_kind,
        quality_flags,
        tokenizer_model,
        max_tokens,
        token_chunk_index,
        input_ids,
        attention_mask,
        text_hash,
        text_char_count,
        source_text_char_count
    FROM {table}
    PREWHERE ticker IN ({ticker_sql})
    WHERE accepted_at_utc >= {start_expr}
      AND accepted_at_utc <= {end_expr}
      AND timestamp_us >= {int(start_us)}
      AND timestamp_us <= {int(end_us)}
    ORDER BY ticker, timestamp_us DESC, accession_number, text_rank, document_id, source_id, token_chunk_index
    LIMIT {row_limit} BY ticker
)
ORDER BY timestamp_us, ticker, accession_number, text_rank, document_id, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return self._token_updates(kind="sec_filing", rows=self._query_json_rows(query), chunks=int(self.config.sec_token_chunks))

    def _fetch_xbrl(self, *, tickers: tuple[str, ...], start_us: int, end_us: int) -> list[LowFrequencyContextUpdate]:
        if not tickers:
            return []
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.sec_xbrl_context_table)}"
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
        query = f"""
SELECT *
FROM
(
    SELECT
        ticker,
        timestamp_us,
        source_id,
        cik,
        issuer_id,
        taxonomy,
        tag,
        unit_code,
        fiscal_year,
        fiscal_period,
        form_type,
        accepted_at_source,
        accession_number,
        period_end_date,
        value,
        calendar_period_code,
        location_code,
        xbrl_row_kind,
        bridge_id,
        mapping_confidence AS mapping_confidence_score
    FROM {table}
    PREWHERE ticker IN ({ticker_sql})
    WHERE timestamp_us >= {int(start_us)}
      AND timestamp_us <= {int(end_us)}
    ORDER BY ticker, timestamp_us DESC, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
    LIMIT {int(self.config.xbrl_items)} BY ticker
)
ORDER BY timestamp_us, ticker, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
FORMAT JSONEachRow
"""
        updates: list[LowFrequencyContextUpdate] = []
        for row in self._query_json_rows(query):
            ticker = str(row.get("ticker", "")).upper()
            ts = int(row.get("timestamp_us", 0) or 0)
            key = (
                "xbrl",
                ticker,
                ts,
                str(row.get("source_id", "")),
                str(row.get("taxonomy", "")),
                str(row.get("tag", "")),
                str(row.get("unit_code", "")),
                str(row.get("period_end_date", "")),
                str(row.get("xbrl_row_kind", "")),
            )
            if not ticker or ts <= 0 or not self._mark_seen(key):
                continue
            numeric = np.asarray(
                [
                    _safe_float(row.get("value")),
                    _safe_float(row.get("fiscal_year")),
                    _date_to_epoch_day(row.get("period_end_date")),
                    _safe_float(row.get("mapping_confidence_score")),
                ],
                dtype=np.float32,
            )
            updates.append(
                LowFrequencyContextUpdate(
                    kind="xbrl",
                    ticker=ticker,
                    timestamp_us=ts,
                    payload=ExternalContextPayload(kind="xbrl", numeric_values=numeric, metadata=dict(row)),
                )
            )
        return updates

    def _fetch_macro_bars(
        self,
        *,
        tickers: tuple[str, ...],
        start_us: int,
        end_us: int,
        include_global_symbols: bool = True,
    ) -> list[LowFrequencyContextUpdate]:
        symbols = {str(ticker).upper() for ticker in tickers if str(ticker).strip()}
        global_symbols = {str(symbol).upper() for symbol in self.config.global_symbols if str(symbol).strip()}
        if include_global_symbols:
            symbols.update(global_symbols)
        if not symbols:
            return []
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.macro_bars_table)}"
        symbol_sql = ", ".join(sql_string(symbol) for symbol in sorted(symbols))
        timeframe_sql = ", ".join(sql_string(tf) for tf in self.config.macro_timeframes)
        query = f"""
SELECT
    sym,
    timeframe,
    toUnixTimestamp64Micro(bar_start) AS bar_start_us,
    toUnixTimestamp64Micro(bar_end) AS timestamp_us,
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
  AND timeframe IN ({timeframe_sql})
  AND bar_end >= {_date_time64_from_us(int(start_us))}
  AND bar_end <= {_date_time64_from_us(int(end_us))}
ORDER BY timestamp_us, sym, timeframe
FORMAT JSONEachRow
"""
        updates: list[LowFrequencyContextUpdate] = []
        for row in self._query_json_rows(query):
            symbol = str(row.get("sym", "")).upper()
            ts = int(row.get("timestamp_us", 0) or 0)
            key = ("macro_bar", symbol, str(row.get("timeframe", "")), int(row.get("bar_start_us", 0) or 0), ts)
            if not symbol or ts <= 0 or not self._mark_seen(key):
                continue
            values = np.asarray(
                [
                    _safe_float(row.get("open")),
                    _safe_float(row.get("high")),
                    _safe_float(row.get("low")),
                    _safe_float(row.get("close")),
                    _safe_float(row.get("volume")),
                    _safe_float(row.get("dollar_volume")),
                    _safe_float(row.get("trade_count")),
                    _safe_float(row.get("quote_count")),
                    _safe_float(row.get("vwap")),
                ],
                dtype=np.float32,
            )
            is_global = symbol in global_symbols
            updates.append(
                LowFrequencyContextUpdate(
                    kind="global_market_bar" if is_global else "ticker_macro_bar",
                    ticker=symbol,
                    timestamp_us=ts,
                    payload=ExternalContextPayload(kind="global_market_bar" if is_global else "ticker_macro_bar", numeric_values=values, metadata=dict(row)),
                )
            )
        return updates

    def _token_updates(
        self,
        *,
        kind: str,
        rows: list[dict[str, Any]],
        chunks: int,
        global_item: bool = False,
    ) -> list[LowFrequencyContextUpdate]:
        grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
        for row in rows:
            ticker = str(row.get("ticker", "")).upper()
            ts = int(row.get("timestamp_us", 0) or 0)
            source_id = str(row.get("source_id", "") or row.get("accession_number", "") or row.get("text_hash", ""))
            if ticker and ts > 0 and source_id:
                grouped.setdefault((ticker, ts, source_id), []).append(row)
        updates: list[LowFrequencyContextUpdate] = []
        for (ticker, ts, source_id), token_rows in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
            key = (kind, ticker, ts, source_id)
            if not self._mark_seen(key):
                continue
            payload = _token_payload_from_rows(kind=kind, rows=token_rows, chunks=chunks, text_max_tokens=int(self.config.text_max_tokens))
            updates.append(LowFrequencyContextUpdate(kind=kind, ticker=ticker, timestamp_us=ts, payload=payload, global_item=global_item))
        return updates

    def _query_json_rows(self, query: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _mark_seen(self, key: tuple[Any, ...]) -> bool:
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        return True


def iter_rows_by_ticker_chronological(rows_by_ticker: dict[str, np.ndarray]) -> Iterable[ReplayEvent]:
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
        yield ReplayEvent(ticker=best_ticker, row=rows_by_ticker[best_ticker][pos])


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


def replay_items_for_block(block: RollingEventBlock, context_updates: Iterable[LowFrequencyContextUpdate]) -> Iterable[TimestampedReplayItem]:
    updates = sorted(context_updates, key=lambda item: (int(item.timestamp_us), str(item.kind), str(item.ticker)))
    update_index = 0
    for event in block.iter_chronological():
        event_ts = int(event.row["sip_timestamp_us"])
        while update_index < len(updates) and int(updates[update_index].timestamp_us) <= event_ts:
            yield TimestampedReplayItem(timestamp_us=int(updates[update_index].timestamp_us), context=updates[update_index])
            update_index += 1
        yield TimestampedReplayItem(timestamp_us=event_ts, event=event)
    while update_index < len(updates):
        yield TimestampedReplayItem(timestamp_us=int(updates[update_index].timestamp_us), context=updates[update_index])
        update_index += 1


def _token_payload_from_rows(*, kind: str, rows: list[dict[str, Any]], chunks: int, text_max_tokens: int) -> ExternalContextPayload:
    token_ids = np.zeros((int(chunks), int(text_max_tokens)), dtype=np.uint32)
    attention_mask = np.zeros((int(chunks), int(text_max_tokens)), dtype=np.uint8)
    metadata = dict(rows[0]) if rows else {}
    for row in rows:
        chunk_index = int(row.get("token_chunk_index", 0) or 0)
        if chunk_index < 0 or chunk_index >= int(chunks):
            continue
        ids = _as_int_array(row.get("input_ids"), dtype=np.uint32)
        mask = _as_int_array(row.get("attention_mask"), dtype=np.uint8)
        length = min(int(text_max_tokens), int(ids.shape[0]), int(mask.shape[0]))
        if length <= 0:
            continue
        token_ids[chunk_index, :length] = ids[:length]
        attention_mask[chunk_index, :length] = mask[:length]
    return ExternalContextPayload(kind=kind, token_ids=token_ids, attention_mask=attention_mask, metadata=metadata)


def _as_int_array(value: Any, *, dtype: Any) -> np.ndarray:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, list):
        value = []
    return np.asarray(value, dtype=dtype)


def _chunks(items: tuple[Any, ...], size: int) -> Iterable[tuple[Any, ...]]:
    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield items[start : start + step]


def _date_time64_from_us(timestamp_us: int) -> str:
    value = dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc)
    text = value.strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(text)}, 6, 'UTC')"


def _date_from_us(timestamp_us: int) -> str:
    return _date_obj_from_us(timestamp_us).isoformat()


def _date_obj_from_us(timestamp_us: int) -> dt.date:
    value = dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc)
    return value.date()


def _date_to_epoch_day(value: Any) -> float:
    if value in {None, ""}:
        return 0.0
    try:
        return float(dt.date.fromisoformat(str(value)[:10]).toordinal())
    except ValueError:
        return 0.0


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
