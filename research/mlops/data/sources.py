from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol

import numpy as np

from research.mlops.clickhouse import quote_ident, sql_string
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, PersistentClickHouseBytesClient, query_settings
from research.mlops.data.market_events import events_from_rows, sort_events
from research.mlops.data.contracts import CompactEvent


class EventSource(Protocol):
    def iter_events(self) -> Iterator[CompactEvent]:
        ...


@dataclass(frozen=True, slots=True)
class InMemoryEventSource:
    events: tuple[CompactEvent, ...]
    sort: bool = True

    def iter_events(self) -> Iterator[CompactEvent]:
        yield from (sort_events(self.events) if self.sort else self.events)


@dataclass(frozen=True, slots=True)
class ClickHouseTickerEventSource:
    client: PersistentClickHouseBytesClient
    database: str
    events_table: str
    ticker: str
    start_us: int
    end_us: int
    max_threads: int = 8
    max_memory_usage: str = "80G"

    def iter_events(self) -> Iterator[CompactEvent]:
        yield from events_from_rows(self.fetch_rows(), ticker=self.ticker)

    def fetch_rows(self) -> np.ndarray:
        table = f"{quote_ident(self.database)}.{quote_ident(self.events_table)}"
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
PREWHERE ticker = {sql_string(self.ticker.upper())}
WHERE sip_timestamp_us >= {int(self.start_us)}
  AND sip_timestamp_us < {int(self.end_us)}
ORDER BY ordinal
{query_settings(self)}
FORMAT RowBinary
"""
        payload = self.client.execute_bytes(query)
        if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
            raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by {EVENT_ROW_DTYPE.itemsize}")
        return np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()


def iter_sources(sources: Iterable[EventSource]) -> Iterator[CompactEvent]:
    for source in sources:
        yield from source.iter_events()

