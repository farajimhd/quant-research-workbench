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
from research.mlops.rolling_loader.synthetic import SyntheticEvent


@dataclass(frozen=True, slots=True)
class ClickHouseReplayConfig:
    database: str = "market_sip_compact"
    events_table: str = "events"
    index_table: str = "train_2019_to_2025"
    date: str = ""
    max_threads: int = 8
    max_memory_usage: str = "80G"


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

    def load_tickers_from_index(self, *, limit: int = 0) -> tuple[str, ...]:
        limit_sql = f" LIMIT {int(limit)}" if int(limit) > 0 else ""
        query = f"""
SELECT ticker
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.index_table)}
ORDER BY ticker
{limit_sql}
FORMAT TSV
"""
        return tuple(line.strip().upper() for line in self.text_client.execute(query).splitlines() if line.strip())

    def fetch_day_rows_by_ticker(self, *, tickers: Iterable[str], date: str) -> dict[str, np.ndarray]:
        ticker_tuple = tuple(str(ticker).upper() for ticker in tickers if str(ticker).strip())
        if not ticker_tuple:
            return {}
        ticker_sql = ", ".join(sql_string(ticker) for ticker in ticker_tuple)
        query = f"""
SELECT
    arrayEnumerate([{ticker_sql}])[indexOf([{ticker_sql}], ticker)] - 1 AS span_id,
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
