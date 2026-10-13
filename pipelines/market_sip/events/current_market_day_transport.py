"""Explicit bounded transport for current producers; no credential fallback.

Factories are constructed by the approved operator assembly. This adapter
never discovers credentials, retries writes, creates tables or opens files.
"""
from dataclasses import dataclass
import json
from uuid import uuid4


@dataclass(frozen=True)
class MarketDayQueryLimits:
    max_threads: int
    max_memory_bytes: int
    timeout_seconds: int
    max_result_rows: int
    max_result_bytes: int

    def __post_init__(self):
        values = tuple(vars(self).values())
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError('Producer query limits must be explicit positive integers')

    def settings(self):
        return dict(max_threads=self.max_threads, max_insert_threads=1,
            max_memory_usage=self.max_memory_bytes,
            max_execution_time=self.timeout_seconds,
            max_result_rows=self.max_result_rows, max_result_bytes=self.max_result_bytes,
            result_overflow_mode='throw',
            # Never spill operational intermediate state onto default storage.
            max_bytes_before_external_group_by=0, max_bytes_before_external_sort=0)


class CurrentMarketDaySqlClient:
    """One owned connection, compatible with shared pure numeric functions.

    Server-side execution timeout bounds failed requests. An ambiguous write
    remains an explicit recovery condition; no retry or speculative replacement
    is attempted. The coordinator drains active operations before closing.
    """
    def __init__(self, http_client, limits):
        if type(limits) is not MarketDayQueryLimits:
            raise ValueError('Current producer requires reviewed query limits')
        self.http = http_client
        self.limits = limits
        self.closed = False

    def query(self, query, label='query', read=True):
        if self.closed:
            raise RuntimeError('Producer connection is closed')
        if type(query) is not str or not query.strip() or type(read) is not bool:
            raise ValueError('Producer query is invalid')
        response = self.http.execute(query + (' FORMAT JSONEachRow' if read else ''),
            query_id='current-market-day-' + uuid4().hex)
        if not read:
            return []
        if len(response.encode('utf-8')) > self.limits.max_result_bytes:
            raise RuntimeError('Producer response exceeds reviewed byte limit')
        lines = response.splitlines()
        if len(lines) > self.limits.max_result_rows:
            raise RuntimeError('Producer response exceeds reviewed row limit')
        rows = [json.loads(line) for line in lines]
        if any(type(row) is not dict for row in rows):
            raise RuntimeError('Producer response is not a named-row result')
        return rows

    def close(self):
        if not self.closed:
            self.closed = True
            self.http.close()


def current_market_day_sql_factory(http_factory, limits):
    """Lazy creation with settings passed explicitly to the role-owned factory.

    The caller must supply a dedicated approved writer principal. Returning
    an adapter performs no I/O until called from the producer's owned worker.
    """
    if not callable(http_factory) or type(limits) is not MarketDayQueryLimits:
        raise ValueError('Current producer transport lacks explicit authorities')
    def owned():
        return CurrentMarketDaySqlClient(http_factory(limits.settings()), limits)
    return owned
