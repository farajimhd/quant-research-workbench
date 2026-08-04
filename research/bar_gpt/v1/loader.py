from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import math
import multiprocessing as mp
import random
import re
import time
import uuid
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Iterator, Mapping
from urllib import error, parse, request
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import torch
from torch.utils.data import DataLoader, Dataset, IterableDataset, get_worker_info

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.corporate_actions import (
    SplitAction,
    cumulative_share_factors,
    normalize_features_to_anchor,
    split_execution_dates,
)
from research.bar_gpt.v1.data import (
    PATHWAY_ID_BY_NAME,
    TIMEFRAME_US_BY_NAME,
    BarGPTExample,
    BarView,
    causal_asof_indices,
    collate_examples,
    densify_one_second_view,
    rollup_calendar_view,
    rollup_intraday_view,
)
from research.bar_gpt.v1.features import project_stationary_features
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES, SESSION_END_SECOND, SESSION_START_SECOND, SESSION_TIMEZONE
from research.bar_gpt.v1.sampling import CoverageCursor, has_condition_target, select_stratified_examples, session_phase
from research.mlops.clickhouse import quote_ident, sql_string


CONDITION_COLUMNS: tuple[str, ...] = (
    "condition_halt_pause_flag",
    "condition_resume_flag",
    "condition_news_risk_flag",
    "condition_luld_limit_state_flag",
)


@dataclass(frozen=True, slots=True)
class ClickHouseBarStreamConfig:
    url: str
    user: str
    password: str
    database: str = "market_sip_compact"
    table: str = "bar_gpt_1s_bars_v1"
    max_threads: int = 4
    max_block_size: int = 65_536
    max_memory_usage: int = 8 * 1024**3
    query_days: int = 7
    max_bytes_before_external_sort: int = 1024**3
    retry_attempts: int = 5
    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 8.0


@dataclass(frozen=True, slots=True)
class TickerInterval:
    canonical_ticker: str
    source_ticker: str
    valid_from: str
    valid_to_exclusive: str


@dataclass(frozen=True, slots=True)
class TickerDateUnit:
    ticker: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class OriginWindow:
    local_date: str
    origin_bucket: int
    prior_date: str | None
    origin_count: int | None = None


@dataclass(frozen=True, slots=True)
class SequentialSessionPlan:
    unit_index: int
    ticker: str
    unit_start_date: str
    unit_end_date: str
    local_date: str
    prior_date: str | None
    first_origin: int
    block_count: int
    unit_block_start: int
    global_block_start: int


@dataclass(frozen=True, slots=True)
class SequentialBlockPlan:
    """Compact exact global ordering for every sequential training block."""

    sessions: tuple[SequentialSessionPlan, ...]
    session_block_starts: tuple[int, ...]
    unit_global_starts: tuple[int, ...]
    unit_block_counts: tuple[int, ...]
    total_blocks: int
    total_origins: int

    def locate(self, global_block_index: int) -> tuple[SequentialSessionPlan, int, int]:
        index = int(global_block_index)
        if index < 0 or index >= self.total_blocks:
            raise IndexError(f"global block index {index} is outside [0,{self.total_blocks})")
        session_index = bisect_right(self.session_block_starts, index) - 1
        session = self.sessions[session_index]
        session_block = index - session.global_block_start
        unit_block = session.unit_block_start + session_block
        return session, session_block, unit_block

    def resume_global_index(self, cursor: CoverageCursor | None) -> int:
        if cursor is None:
            return 0
        unit = int(cursor.unit_index)
        if unit < 0 or unit >= len(self.unit_global_starts):
            raise ValueError(f"resume unit {unit} is outside the sequential plan")
        block = int(cursor.block_offset)
        if block < -1 or block >= self.unit_block_counts[unit]:
            raise ValueError(
                f"resume block {block} is outside unit {unit} with {self.unit_block_counts[unit]} blocks"
            )
        return self.unit_global_starts[unit] + block + 1

    def cursor_global_index(self, cursor: CoverageCursor) -> int:
        unit = int(cursor.unit_index)
        block = int(cursor.block_offset)
        if unit < 0 or unit >= len(self.unit_global_starts):
            raise ValueError(f"cursor unit {unit} is outside the sequential plan")
        if block < 0 or block >= self.unit_block_counts[unit]:
            raise ValueError(f"cursor block {block} is outside unit {unit}")
        return self.unit_global_starts[unit] + block


def month_units(start_date: str, end_date: str, tickers: tuple[str, ...], *, seed: int) -> list[TickerDateUnit]:
    """Return chronological months with a deterministic ticker shuffle inside each month."""
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    units: list[TickerDateUnit] = []
    cursor = start
    while cursor < end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        right = min(end, next_month)
        selected = list(tickers)
        random.Random(f"{seed}:{cursor:%Y-%m}").shuffle(selected)
        units.extend(TickerDateUnit(ticker, cursor.isoformat(), right.isoformat()) for ticker in selected)
        cursor = right
    return units


def worker_ticker_shards(tickers: tuple[str, ...], *, workers: int, seed: int) -> tuple[tuple[str, ...], ...]:
    """Assign each ticker to one worker for an epoch so reference caches are never duplicated."""
    if workers <= 0:
        return (tuple(tickers),)
    ordered = list(dict.fromkeys(tickers))
    random.Random(f"{seed}:worker-ticker-shards").shuffle(ordered)
    shards: list[list[str]] = [[] for _ in range(int(workers))]
    for index, ticker in enumerate(ordered):
        shards[index % int(workers)].append(ticker)
    return tuple(tuple(values) for values in shards)


def origin_window_schedule(
    *,
    dates: list[str],
    start_date: str,
    end_date: str,
    count: int,
    context_bars: int,
    origin_bars: int,
    right_support_bars: int,
    conditions_by_date: Mapping[str, torch.Tensor],
    seed: int,
) -> tuple[OriginWindow, ...]:
    """Create deterministic, phase-spread bounded origin windows without scanning a ticker-month."""
    ordered = sorted(dict.fromkeys(dates))
    eligible = [day for day in ordered if start_date <= day < end_date]
    if count <= 0 or not eligible:
        return ()
    previous = {day: ordered[index - 1] if index else None for index, day in enumerate(ordered)}
    maximum_start = SESSION_END_SECOND - int(origin_bars) - int(right_support_bars)
    if maximum_start < SESSION_START_SECOND:
        raise ValueError("origin and target support do not fit inside the exchange session")

    phase_ranges = (
        (SESSION_START_SECOND, 9 * 3600 + 30 * 60 - 1),
        (9 * 3600 + 30 * 60, 10 * 3600 + 30 * 60 - 1),
        (10 * 3600 + 30 * 60, 15 * 3600 - 1),
        (15 * 3600, 16 * 3600 - 1),
        (16 * 3600, maximum_start),
    )
    selected: list[OriginWindow] = []
    seen: set[tuple[str, int]] = set()

    def add(day: str, bucket: int) -> None:
        bucket = min(maximum_start, max(SESSION_START_SECOND, int(bucket)))
        if SESSION_START_SECOND < bucket < SESSION_START_SECOND + int(context_bars):
            bucket = SESSION_START_SECOND
        key = (day, bucket)
        if key not in seen and len(selected) < count:
            seen.add(key)
            selected.append(OriginWindow(day, bucket, previous.get(day)))

    # Rare condition windows are deliberately scheduled before ordinary phase coverage.
    for day in eligible:
        flags = conditions_by_date.get(day)
        if flags is None or not bool(torch.any(flags > 0)):
            continue
        indices = torch.nonzero(flags.gt(0).any(dim=-1), as_tuple=False).flatten()
        for index in indices.tolist():
            condition_bucket = SESSION_START_SECOND + int(index)
            add(day, condition_bucket - int(origin_bars) - min(5, int(right_support_bars)))

    attempt = 0
    maximum_attempts = max(count * 8, len(eligible) * len(phase_ranges) * 2)
    while len(selected) < count and attempt < maximum_attempts:
        slot = attempt
        day_index = min(len(eligible) - 1, ((2 * slot + 1) * len(eligible)) // max(2, 2 * count))
        day = eligible[day_index]
        phase_index = slot % len(phase_ranges)
        left, right = phase_ranges[phase_index]
        left = min(left, maximum_start)
        right = min(right, maximum_start)
        if right < left:
            attempt += 1
            continue
        digest = hashlib.sha256(f"{seed}:{day}:{phase_index}:{attempt}".encode("utf-8")).digest()
        width = right - left + 1
        add(day, left + int.from_bytes(digest[:8], "big") % max(1, width))
        attempt += 1
    return tuple(selected)


def provider_timeline_intervals(
    canonical_ticker: str,
    rows: list[tuple[str, str, str]],
    *,
    coverage_start: str,
) -> tuple[TickerInterval, ...]:
    canonical = canonical_ticker.upper()
    if len({row[0] for row in rows}) != 1:
        return ()
    events_by_date: dict[str, set[str]] = {}
    for _entity_key, event_date, source in rows:
        if event_date and source:
            events_by_date.setdefault(event_date, set()).add(source.upper())
    if any(len(sources) != 1 for sources in events_by_date.values()):
        raise RuntimeError(f"conflicting provider ticker events for {canonical}")
    timeline = sorted((day, next(iter(sources))) for day, sources in events_by_date.items())
    if not timeline:
        timeline = [(coverage_start, canonical)]
    if timeline[-1][1] != canonical:
        raise RuntimeError(f"provider ticker timeline for {canonical} ends at {timeline[-1][1]}")
    return tuple(
        TickerInterval(
            canonical,
            source,
            left,
            timeline[index + 1][0] if index + 1 < len(timeline) else "9999-12-31",
        )
        for index, (left, source) in enumerate(timeline)
    )


def ticker_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    source_intervals: tuple[TickerInterval, ...] = (),
) -> str:
    intervals = source_intervals or (
        TickerInterval(ticker.upper(), ticker.upper(), start_date, end_date),
    )
    predicates = []
    for interval in intervals:
        left = max(start_date, interval.valid_from)
        right = min(end_date, interval.valid_to_exclusive)
        if left < right:
            predicates.append(
                f"(b.ticker = {sql_string(interval.source_ticker)} AND "
                f"b.local_date >= toDate({sql_string(left)}) AND b.local_date < toDate({sql_string(right)}))"
            )
    if not predicates:
        raise ValueError(f"no point-in-time source interval covers {ticker} in [{start_date},{end_date})")
    columns = ",\n    ".join(
        (
            "local_date",
            f"{sql_string(ticker.upper())} AS ticker",
            "bar_start_us",
            "bar_end_us",
            "available_at_us",
            *FEATURE_NAMES,
        )
    )
    return f"""
SELECT
    {columns}
FROM {quote_ident(config.database)}.{quote_ident(config.table)} AS b
PREWHERE {' OR '.join(predicates)}
ORDER BY b.ticker, b.local_date, b.bucket_index
SETTINGS
    max_threads = {max(1, int(config.max_threads))},
    max_block_size = {max(1, int(config.max_block_size))},
    max_memory_usage = {max(1, int(config.max_memory_usage))},
    max_bytes_before_external_sort = {max(1, int(config.max_bytes_before_external_sort))},
    optimize_read_in_order = 1
FORMAT ArrowStream
"""


def origin_windows_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    windows: tuple[OriginWindow, ...],
    source_intervals: tuple[TickerInterval, ...],
    context_bars: int,
    origin_bars: int,
    right_support_bars: int,
) -> str:
    """Read only causal context, visible origins, and target-only support for bounded windows."""
    if not windows:
        raise ValueError("at least one origin window is required")

    def source_for(day: str) -> str:
        matches = {
            interval.source_ticker
            for interval in source_intervals
            if interval.valid_from <= day < interval.valid_to_exclusive
        }
        if len(matches) != 1:
            raise RuntimeError(f"point-in-time source identity for {ticker} on {day} is {sorted(matches)}")
        return next(iter(matches))

    ranges: set[tuple[str, str, int, int]] = set()
    for window in windows:
        elapsed = int(window.origin_bucket) - SESSION_START_SECOND
        prior_rows = max(0, int(context_bars) - elapsed)
        target_start = max(SESSION_START_SECOND, int(window.origin_bucket) - int(context_bars))
        visible_origins = int(window.origin_count) if window.origin_count is not None else int(origin_bars)
        target_end = min(
            SESSION_END_SECOND,
            int(window.origin_bucket) + visible_origins + int(right_support_bars),
        )
        if visible_origins <= 0 or int(window.origin_bucket) + visible_origins > SESSION_END_SECOND:
            raise ValueError(f"invalid visible origin interval: {window}")
        ranges.add((source_for(window.local_date), window.local_date, target_start, target_end))
        if prior_rows:
            if window.prior_date is None:
                continue
            ranges.add(
                (
                    source_for(window.prior_date),
                    window.prior_date,
                    SESSION_END_SECOND - prior_rows,
                    SESSION_END_SECOND,
                )
            )
    predicates = [
        f"(b.ticker={sql_string(source)} AND b.local_date=toDate({sql_string(day)}) "
        f"AND b.bucket_index>={left} AND b.bucket_index<{right})"
        for source, day, left, right in sorted(ranges)
    ]
    columns = ",\n    ".join(
        (
            "local_date",
            f"{sql_string(ticker.upper())} AS ticker",
            "bucket_index",
            "bar_start_us",
            "bar_end_us",
            "available_at_us",
            *FEATURE_NAMES,
        )
    )
    return f"""
SELECT
    {columns}
FROM {quote_ident(config.database)}.{quote_ident(config.table)} AS b
PREWHERE {' OR '.join(predicates)}
ORDER BY b.ticker, b.local_date, b.bucket_index
SETTINGS
    max_threads = {max(1, int(config.max_threads))},
    max_block_size = {max(1, int(config.max_block_size))},
    max_memory_usage = {max(1, int(config.max_memory_usage))},
    optimize_read_in_order = 1
FORMAT ArrowStream
"""


def condition_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    condition_table: str,
    source_intervals: tuple[TickerInterval, ...],
) -> str:
    predicates: list[str] = []
    for interval in source_intervals:
        left = max(start_date, interval.valid_from)
        right = min(end_date, interval.valid_to_exclusive)
        if left < right:
            predicates.append(
                f"(ticker={sql_string(interval.source_ticker)} AND local_date>=toDate({sql_string(left)}) "
                f"AND local_date<toDate({sql_string(right)}))"
            )
    if not predicates:
        raise ValueError(f"no condition source interval covers {ticker} in [{start_date},{end_date})")
    return f"""
SELECT local_date, bucket_index, {', '.join(CONDITION_COLUMNS)}
FROM {quote_ident(config.database)}.{quote_ident(condition_table)}
PREWHERE ({' OR '.join(predicates)}) AND label_resolution_us=1000000
ORDER BY local_date, bucket_index
SETTINGS max_threads={max(1, int(config.max_threads))}, max_block_size={max(1, int(config.max_block_size))}
FORMAT ArrowStream
"""


def identity_intervals_query(
    *,
    tickers: tuple[str, ...],
    identity_database: str,
    interval_table: str,
    entity_table: str,
) -> str:
    selected = ", ".join(sql_string(ticker.upper()) for ticker in tickers)
    return f"""
SELECT upper(e.current_ticker) AS canonical_ticker,
       upper(i.ticker_normalized) AS source_ticker,
       toString(i.valid_from_date) AS valid_from,
       toString(ifNull(i.valid_to_date_exclusive, toDate('9999-12-31'))) AS valid_to_exclusive
FROM (SELECT provider_entity_key,current_ticker
      FROM {quote_ident(identity_database)}.{quote_ident(entity_table)} FINAL
      WHERE is_deleted=0 AND upper(current_ticker) IN ({selected})) AS e
INNER JOIN (SELECT provider_entity_key,ticker_normalized,valid_from_date,valid_to_date_exclusive
            FROM {quote_ident(identity_database)}.{quote_ident(interval_table)} FINAL
            WHERE is_deleted=0 AND mapping_status='mapped') AS i USING provider_entity_key
ORDER BY canonical_ticker, valid_from, source_ticker
FORMAT ArrowStream
"""


def split_actions_query(
    *,
    source_tickers: tuple[str, ...],
    start_date: str,
    end_date: str,
    split_database: str,
    split_table: str,
) -> str:
    selected = ", ".join(sql_string(ticker.upper()) for ticker in source_tickers)
    return f"""
SELECT DISTINCT upper(s.provider_ticker) AS source_ticker,
       toString(s.execution_date) AS execution_date,
       toFloat64(s.split_from) AS split_from,
       toFloat64(s.split_to) AS split_to
FROM {quote_ident(split_database)}.{quote_ident(split_table)} AS s FINAL
WHERE upper(s.provider_ticker) IN ({selected})
  AND s.execution_date >= toDate({sql_string(start_date)})
  AND s.execution_date < toDate({sql_string(end_date)})
  AND s.split_from > 0 AND s.split_to > 0 AND s.split_from != s.split_to
ORDER BY source_ticker, execution_date, split_from, split_to
FORMAT ArrowStream
"""


def provider_ticker_intervals_query(
    *,
    tickers: tuple[str, ...],
    identity_database: str,
    entity_table: str,
    event_table: str,
) -> str:
    selected = ", ".join(sql_string(ticker.upper()) for ticker in tickers)
    return f"""
SELECT upper(e.current_ticker) AS canonical_ticker,
       e.provider_entity_key,
       ifNull(toString(v.event_date), '') AS event_date,
       upper(ifNull(v.ticker, '')) AS source_ticker
FROM (SELECT provider_entity_key,current_ticker
      FROM {quote_ident(identity_database)}.{quote_ident(entity_table)} FINAL
      WHERE is_deleted=0 AND upper(current_ticker) IN ({selected})) AS e
LEFT JOIN (SELECT provider_entity_key,event_date,ticker
           FROM {quote_ident(identity_database)}.{quote_ident(event_table)} FINAL
           WHERE is_deleted=0) AS v USING provider_entity_key
ORDER BY canonical_ticker, event_date, source_ticker
SETTINGS join_use_nulls=1
FORMAT ArrowStream
"""


def certified_ranges_query(database: str, manifest_table: str, target_table: str) -> str:
    return f"""
SELECT
    local_date AS partition_month,
    unit_id,
    message,
    output_row_count
FROM {quote_ident(database)}.{quote_ident(manifest_table)} FINAL
WHERE artifact_name = {sql_string(target_table)}
  AND status = 'certified_range'
ORDER BY local_date, unit_id
FORMAT JSONEachRow
"""


def daily_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    daily_table: str = "daily_session_bars_by_symbol_time_v1",
    source_intervals: tuple[TickerInterval, ...] = (),
) -> str:
    intervals = source_intervals or (TickerInterval(ticker.upper(), ticker.upper(), start_date, end_date),)
    predicates = daily_source_interval_predicates(intervals, start_date=start_date, end_date=end_date)
    if not predicates:
        raise ValueError(f"no point-in-time daily source interval covers {ticker} in [{start_date},{end_date})")
    columns = ",\n    ".join(("session_date AS local_date", f"{sql_string(ticker.upper())} AS ticker", "session_kind", "bar_start_us", "bar_end_us", "available_at_us", *FEATURE_NAMES))
    return f"""
SELECT
    {columns}
FROM {quote_ident(config.database)}.{quote_ident(daily_table)} FINAL
PREWHERE session_date >= toDate({sql_string(start_date)})
  AND session_date < toDate({sql_string(end_date)})
WHERE {' OR '.join(predicate for predicate, _canonical in predicates)}
ORDER BY local_date, bar_start_us
SETTINGS max_threads = {max(1, int(config.max_threads))}, max_block_size = {max(1, int(config.max_block_size))}
FORMAT ArrowStream
"""


def daily_tickers_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    tickers: tuple[str, ...],
    start_date: str,
    end_date: str,
    daily_table: str = "daily_session_bars_by_symbol_time_v1",
    intervals_by_ticker: Mapping[str, tuple[TickerInterval, ...]] | None = None,
) -> str:
    if not tickers:
        raise ValueError("at least one daily ticker is required")
    resolved = intervals_by_ticker or {
        ticker.upper(): (TickerInterval(ticker.upper(), ticker.upper(), start_date, end_date),) for ticker in tickers
    }
    validate_unique_source_intervals(intervals_by_ticker=resolved, start_date=start_date, end_date=end_date)
    predicates: list[tuple[str, str]] = []
    for ticker in tickers:
        canonical = ticker.upper()
        intervals = resolved.get(canonical)
        if not intervals:
            raise ValueError(f"missing point-in-time daily source intervals for {canonical}")
        predicates.extend(daily_source_interval_predicates(intervals, start_date=start_date, end_date=end_date))
    if not predicates:
        raise ValueError(f"no point-in-time daily source intervals cover [{start_date},{end_date})")
    ticker_expression = "multiIf(" + ", ".join(
        f"{predicate}, {sql_string(canonical)}" for predicate, canonical in predicates
    ) + ", source_ticker)"
    columns = ",\n    ".join(
        (
            "session_date AS local_date",
            f"{ticker_expression} AS ticker",
            "session_kind",
            "bar_start_us",
            "bar_end_us",
            "available_at_us",
            *FEATURE_NAMES,
        )
    )
    return f"""
SELECT
    {columns}
FROM {quote_ident(config.database)}.{quote_ident(daily_table)} FINAL
PREWHERE session_date >= toDate({sql_string(start_date)})
  AND session_date < toDate({sql_string(end_date)})
WHERE {' OR '.join(predicate for predicate, _canonical in predicates)}
ORDER BY ticker, local_date, bar_start_us
SETTINGS max_threads = {max(1, int(config.max_threads))}, max_block_size = {max(1, int(config.max_block_size))}
FORMAT ArrowStream
"""


def daily_source_interval_predicates(
    intervals: tuple[TickerInterval, ...],
    *,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str]]:
    predicates: list[tuple[str, str]] = []
    for interval in intervals:
        left = max(start_date, interval.valid_from)
        right = min(end_date, interval.valid_to_exclusive)
        if left >= right:
            continue
        predicate = (
            f"(source_ticker = {sql_string(interval.source_ticker)} AND "
            f"session_date >= toDate({sql_string(left)}) AND session_date < toDate({sql_string(right)}))"
        )
        predicates.append((predicate, interval.canonical_ticker))
    return predicates


def validate_unique_source_intervals(
    *,
    intervals_by_ticker: Mapping[str, tuple[TickerInterval, ...]],
    start_date: str,
    end_date: str,
) -> None:
    by_source: dict[str, list[TickerInterval]] = {}
    for intervals in intervals_by_ticker.values():
        for interval in intervals:
            if max(start_date, interval.valid_from) < min(end_date, interval.valid_to_exclusive):
                by_source.setdefault(interval.source_ticker, []).append(interval)
    for source_ticker, intervals in by_source.items():
        ordered = sorted(intervals, key=lambda item: (item.valid_from, item.valid_to_exclusive, item.canonical_ticker))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if right.valid_from >= left.valid_to_exclusive:
                    break
                if left.canonical_ticker != right.canonical_ticker:
                    raise RuntimeError(
                        f"point-in-time source ticker {source_ticker} overlaps canonical identities "
                        f"{left.canonical_ticker} and {right.canonical_ticker}"
                    )


class ArrowStreamClient:
    """Incremental ClickHouse ArrowStream reader; response bodies are never read_all()."""

    def __init__(self, config: ClickHouseBarStreamConfig) -> None:
        self.config = config

    @staticmethod
    def _retryable_read_error(exc: BaseException) -> bool:
        if isinstance(exc, error.HTTPError):
            return exc.code == 429 or 500 <= exc.code < 600
        return isinstance(
            exc,
            (
                error.URLError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                ConnectionError,
                ConnectionResetError,
                BrokenPipeError,
                TimeoutError,
                OSError,
            ),
        ) or exc.__class__.__name__ in {"ArrowIOError", "ArrowInvalid", "ArrowCapacityError"}

    @contextmanager
    def record_batches(self, sql: str, *, query_id: str | None = None):
        """Return one complete bounded Arrow response, retrying before exposing any rows.

        A partially consumed Arrow stream is never yielded to callers. This is
        what makes a retry exact: the failed attempt contributes zero rows, so
        a repeated query cannot duplicate a training block.
        """
        query = sql.strip().rstrip(";")
        if not re.search(r"\bFORMAT\s+ArrowStream\s*$", query, flags=re.IGNORECASE):
            raise ValueError("ArrowStreamClient requires FORMAT ArrowStream")
        logical_id = query_id or f"bar_gpt_arrow_{uuid.uuid4().hex}"
        attempts = max(1, int(self.config.retry_attempts))
        buffered: tuple[object, ...] | None = None
        for attempt in range(attempts):
            identifier = f"{logical_id}_attempt_{attempt + 1}"
            url = self.config.url.rstrip("/") + "/?" + parse.urlencode({"query_id": identifier})
            req = request.Request(url, data=query.encode("utf-8"), method="POST")
            if self.config.user:
                req.add_header("X-ClickHouse-User", self.config.user)
            if self.config.password:
                req.add_header("X-ClickHouse-Key", self.config.password)
            response = None
            try:
                response = request.urlopen(req, timeout=None)
                import pyarrow as pa

                buffered = tuple(pa.ipc.open_stream(response))
                break
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if not self._retryable_read_error(exc) or attempt + 1 >= attempts:
                    raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc
            except BaseException as exc:
                if not self._retryable_read_error(exc) or attempt + 1 >= attempts:
                    raise
            finally:
                if response is not None:
                    response.close()
            delay = min(
                float(self.config.retry_max_seconds),
                float(self.config.retry_initial_seconds) * (2**attempt),
            )
            if delay > 0:
                time.sleep(delay)
        if buffered is None:
            raise RuntimeError("ClickHouse Arrow response exhausted retries without a result")
        yield iter(buffered)

    def iter_session_views(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        source_intervals: tuple[TickerInterval, ...] = (),
        device: torch.device | str = "cpu",
    ) -> Iterator[tuple[str, BarView]]:
        cursor = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        while cursor < end:
            right = min(end, cursor + dt.timedelta(days=max(1, int(self.config.query_days))))
            query = ticker_range_query(
                self.config,
                ticker=ticker,
                start_date=cursor.isoformat(),
                end_date=right.isoformat(),
                source_intervals=source_intervals,
            )
            current_date = ""
            current_frames: list[pl.DataFrame] = []
            with self.record_batches(query) as batches:
                for batch in batches:
                    frame = pl.from_arrow(batch)
                    if frame.is_empty():
                        continue
                    for part in frame.partition_by("local_date", maintain_order=True):
                        date_value = str(part["local_date"][0])
                        if current_date and date_value != current_date:
                            yield current_date, frame_to_dense_view(pl.concat(current_frames, how="vertical"), device=device)
                            current_frames = []
                        current_date = date_value
                        current_frames.append(part)
            if current_frames:
                yield current_date, frame_to_dense_view(pl.concat(current_frames, how="vertical"), device=device)
            cursor = right

    def read_origin_windows(
        self,
        *,
        ticker: str,
        windows: tuple[OriginWindow, ...],
        source_intervals: tuple[TickerInterval, ...],
        context_bars: int,
        origin_bars: int,
        right_support_bars: int,
        device: torch.device | str = "cpu",
    ) -> list[tuple[BarView, BarView | None]]:
        query = origin_windows_query(
            self.config,
            ticker=ticker,
            windows=windows,
            source_intervals=source_intervals,
            context_bars=context_bars,
            origin_bars=origin_bars,
            right_support_bars=right_support_bars,
        )
        frames: list[pl.DataFrame] = []
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if not frame.is_empty():
                    frames.append(frame)
        combined = pl.concat(frames, how="vertical") if frames else None
        frames_by_date = (
            {
                str(part["local_date"][0]): part
                for part in combined.partition_by("local_date", maintain_order=True)
            }
            if combined is not None
            else {}
        )

        def window_view(day: str, left: int, right: int) -> BarView:
            frame = frames_by_date.get(day)
            selected = frame.filter(
                (pl.col("bucket_index") >= left) & (pl.col("bucket_index") < right)
            ) if frame is not None else None
            return frame_to_dense_window(
                selected,
                ticker=ticker,
                local_date=day,
                clock_start_second=left,
                clock_end_second=right,
                device=device,
            )

        result: list[tuple[BarView, BarView | None]] = []
        for window in windows:
            elapsed = int(window.origin_bucket) - SESSION_START_SECOND
            prior_rows = max(0, int(context_bars) - elapsed)
            target_start = max(SESSION_START_SECOND, int(window.origin_bucket) - int(context_bars))
            visible_origins = int(window.origin_count) if window.origin_count is not None else int(origin_bars)
            target_end = min(
                SESSION_END_SECOND,
                int(window.origin_bucket) + visible_origins + int(right_support_bars),
            )
            target = window_view(window.local_date, target_start, target_end)
            prior = None
            if prior_rows and window.prior_date is not None:
                prior = window_view(
                    window.prior_date,
                    SESSION_END_SECOND - prior_rows,
                    SESSION_END_SECOND,
                )
            result.append((target, prior))
        return result

    def read_condition_views(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        condition_table: str,
        source_intervals: tuple[TickerInterval, ...],
        device: torch.device | str = "cpu",
    ) -> dict[str, torch.Tensor]:
        result: dict[str, torch.Tensor] = {}
        query = condition_range_query(
            self.config,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            condition_table=condition_table,
            source_intervals=source_intervals,
        )
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                for part in frame.partition_by("local_date", maintain_order=True):
                    day = str(part["local_date"][0])
                    dense = result.setdefault(
                        day,
                        torch.zeros((SESSION_END_SECOND - SESSION_START_SECOND, len(CONDITION_COLUMNS)), dtype=torch.float32, device=device),
                    )
                    indices = torch.as_tensor(
                        np.array(part["bucket_index"].to_numpy(), copy=True), dtype=torch.long, device=device
                    ) - SESSION_START_SECOND
                    valid = (indices >= 0) & (indices < dense.shape[0])
                    if torch.any(valid):
                        values = torch.as_tensor(
                            np.array(part.select(list(CONDITION_COLUMNS)).to_numpy(), dtype=np.float32, copy=True),
                            device=device,
                        )
                        dense[indices[valid]] = torch.maximum(dense[indices[valid]], values[valid])
        return result

    def read_daily_view(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        daily_table: str,
        source_intervals: tuple[TickerInterval, ...] = (),
        device: torch.device | str = "cpu",
    ) -> tuple[list[str], BarView] | None:
        frames: list[pl.DataFrame] = []
        query = daily_range_query(
            self.config,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            daily_table=daily_table,
            source_intervals=source_intervals,
        )
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if not frame.is_empty():
                    frames.append(frame)
        if not frames:
            return None
        return daily_session_frame_to_view(pl.concat(frames, how="vertical"), device=device)

    def read_daily_views(
        self,
        *,
        tickers: tuple[str, ...],
        start_date: str,
        end_date: str,
        daily_table: str,
        intervals_by_ticker: Mapping[str, tuple[TickerInterval, ...]],
        device: torch.device | str = "cpu",
    ) -> dict[str, tuple[list[str], BarView]]:
        frames: list[pl.DataFrame] = []
        query = daily_tickers_range_query(
            self.config,
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
            daily_table=daily_table,
            intervals_by_ticker=intervals_by_ticker,
        )
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if not frame.is_empty():
                    frames.append(frame)
        if not frames:
            return {}
        combined = pl.concat(frames, how="vertical")
        result: dict[str, tuple[list[str], BarView]] = {}
        for part in combined.partition_by("ticker", maintain_order=True):
            ticker = str(part["ticker"][0]).upper()
            result[ticker] = daily_session_frame_to_view(part, device=device)
        return result

    def read_identity_intervals(
        self,
        tickers: tuple[str, ...],
        *,
        identity_database: str,
        interval_table: str,
        entity_table: str,
        event_table: str = "market_ticker_event_v1",
        coverage_start: str = "0001-01-01",
    ) -> dict[str, tuple[TickerInterval, ...]]:
        if not tickers:
            return {}
        query = identity_intervals_query(
            tickers=tickers,
            identity_database=identity_database,
            interval_table=interval_table,
            entity_table=entity_table,
        )
        values: dict[str, list[TickerInterval]] = {ticker.upper(): [] for ticker in tickers}
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                for row in frame.iter_rows(named=True):
                    interval = TickerInterval(
                        canonical_ticker=str(row["canonical_ticker"]).upper(),
                        source_ticker=str(row["source_ticker"]).upper(),
                        valid_from=str(row["valid_from"]),
                        valid_to_exclusive=str(row["valid_to_exclusive"]),
                    )
                    values.setdefault(interval.canonical_ticker, []).append(interval)
        missing = tuple(sorted(ticker.upper() for ticker in tickers if not values.get(ticker.upper())))
        if missing:
            fallback_query = provider_ticker_intervals_query(
                tickers=missing,
                identity_database=identity_database,
                entity_table=entity_table,
                event_table=event_table,
            )
            provider_rows: dict[str, list[tuple[str, str, str]]] = {}
            with self.record_batches(fallback_query) as batches:
                for batch in batches:
                    frame = pl.from_arrow(batch)
                    for row in frame.iter_rows(named=True):
                        canonical = str(row["canonical_ticker"]).upper()
                        provider_rows.setdefault(canonical, []).append(
                            (str(row["provider_entity_key"]), str(row["event_date"]), str(row["source_ticker"]).upper())
                        )
            for canonical in missing:
                rows = provider_rows.get(canonical, [])
                values[canonical].extend(
                    provider_timeline_intervals(canonical, rows, coverage_start=coverage_start)
                )

        result: dict[str, tuple[TickerInterval, ...]] = {}
        missing_final: list[str] = []
        for ticker in tickers:
            canonical = ticker.upper()
            intervals = sorted(values.get(canonical, []), key=lambda item: (item.valid_from, item.source_ticker))
            if not intervals:
                missing_final.append(canonical)
                continue
            if intervals[0].source_ticker == canonical and intervals[0].valid_from > coverage_start:
                first = intervals[0]
                intervals[0] = TickerInterval(canonical, canonical, coverage_start, first.valid_to_exclusive)
            for previous, current in zip(intervals, intervals[1:]):
                if previous.valid_to_exclusive > current.valid_from:
                    raise RuntimeError(f"overlapping point-in-time ticker intervals for {canonical}")
            result[canonical] = tuple(intervals)
        if missing_final:
            raise RuntimeError(f"missing point-in-time identity intervals for canonical tickers: {','.join(missing_final)}")
        return result

    def read_split_actions(
        self,
        intervals_by_ticker: Mapping[str, tuple[TickerInterval, ...]],
        *,
        start_date: str,
        end_date: str,
        split_database: str,
        split_table: str,
    ) -> dict[str, tuple[SplitAction, ...]]:
        source_tickers = tuple(sorted({item.source_ticker for values in intervals_by_ticker.values() for item in values}))
        if not source_tickers:
            return {ticker: () for ticker in intervals_by_ticker}
        source_map: dict[str, list[TickerInterval]] = {}
        for values in intervals_by_ticker.values():
            for interval in values:
                source_map.setdefault(interval.source_ticker, []).append(interval)
        query = split_actions_query(
            source_tickers=source_tickers,
            start_date=start_date,
            end_date=end_date,
            split_database=split_database,
            split_table=split_table,
        )
        records: dict[tuple[str, str], set[tuple[float, str]]] = {}
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                for row in frame.iter_rows(named=True):
                    source = str(row["source_ticker"]).upper()
                    execution_date = str(row["execution_date"])
                    matches = [
                        item
                        for item in source_map.get(source, [])
                        if item.valid_from <= execution_date < item.valid_to_exclusive
                    ]
                    canonical = sorted({item.canonical_ticker for item in matches})
                    if len(canonical) != 1:
                        raise RuntimeError(
                            f"split identity is not unique for {source} on {execution_date}: {canonical or 'unmapped'}"
                        )
                    factor = float(row["split_to"]) / float(row["split_from"])
                    records.setdefault((canonical[0], execution_date), set()).add((factor, source))
        result: dict[str, list[SplitAction]] = {ticker: [] for ticker in intervals_by_ticker}
        timezone = ZoneInfo(SESSION_TIMEZONE)
        for (canonical, execution_date), values in sorted(records.items()):
            factors = {round(item[0], 12) for item in values}
            if len(factors) != 1:
                raise RuntimeError(f"conflicting split ratios for {canonical} on {execution_date}: {sorted(factors)}")
            day = dt.date.fromisoformat(execution_date)
            effective = dt.datetime.combine(
                day,
                dt.time(hour=SESSION_START_SECOND // 3600),
                tzinfo=timezone,
            )
            result[canonical].append(
                SplitAction(
                    effective_at_us=int(effective.timestamp() * 1_000_000),
                    share_factor=next(iter(factors)),
                    execution_date=execution_date,
                    source_ticker=sorted(item[1] for item in values)[0],
                )
            )
        return {ticker: tuple(values) for ticker, values in result.items()}


def frame_to_dense_view(frame: pl.DataFrame, *, device: torch.device | str = "cpu") -> BarView:
    if frame.is_empty():
        raise ValueError("cannot convert an empty bar frame")
    if frame["ticker"].n_unique() != 1 or frame["local_date"].n_unique() != 1:
        raise ValueError("frame_to_dense_view requires one ticker/session")
    frame = frame.sort("bar_start_us")
    feature_array = np.array(frame.select(list(FEATURE_NAMES)).to_numpy(), dtype=np.float32, copy=True)
    sparse = BarView(
        features=torch.as_tensor(feature_array, device=device),
        bar_start_us=torch.as_tensor(np.array(frame["bar_start_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(frame["bar_end_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(frame["available_at_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
    )
    local_date = dt.date.fromisoformat(str(frame["local_date"][0]))
    midnight = dt.datetime.combine(local_date, dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE))
    clock_start_us = int((midnight + dt.timedelta(seconds=SESSION_START_SECOND)).timestamp() * 1_000_000)
    clock_end_us = int((midnight + dt.timedelta(seconds=SESSION_END_SECOND)).timestamp() * 1_000_000)
    return densify_one_second_view(sparse, clock_start_us=clock_start_us, clock_end_us=clock_end_us)


def frame_to_dense_window(
    frame: pl.DataFrame | None,
    *,
    ticker: str,
    local_date: str,
    clock_start_second: int,
    clock_end_second: int,
    device: torch.device | str = "cpu",
) -> BarView:
    """Densify one bounded exchange-session window, including windows with no source events."""
    if not SESSION_START_SECOND <= clock_start_second < clock_end_second <= SESSION_END_SECOND:
        raise ValueError("dense origin window must stay inside the configured exchange session")
    day = dt.date.fromisoformat(local_date)
    midnight = dt.datetime.combine(day, dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE))
    clock_start_us = int((midnight + dt.timedelta(seconds=int(clock_start_second))).timestamp() * 1_000_000)
    clock_end_us = int((midnight + dt.timedelta(seconds=int(clock_end_second))).timestamp() * 1_000_000)
    if frame is None or frame.is_empty():
        starts = torch.arange(
            clock_start_us,
            clock_end_us,
            1_000_000,
            dtype=torch.long,
            device=device,
        )
        return BarView(
            features=torch.zeros((starts.numel(), len(FEATURE_NAMES)), dtype=torch.float32, device=device),
            bar_start_us=starts,
            bar_end_us=starts + 1_000_000,
            available_at_us=starts + 1_000_000,
        )
    if frame["ticker"].n_unique() != 1 or str(frame["ticker"][0]).upper() != ticker.upper():
        raise ValueError("origin-window frame has an unexpected ticker identity")
    feature_array = np.array(frame.select(list(FEATURE_NAMES)).to_numpy(), dtype=np.float32, copy=True)
    sparse = BarView(
        features=torch.as_tensor(feature_array, device=device),
        bar_start_us=torch.as_tensor(np.array(frame["bar_start_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(frame["bar_end_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(frame["available_at_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
    )
    return densify_one_second_view(sparse, clock_start_us=clock_start_us, clock_end_us=clock_end_us)


def daily_family_frame_to_view(
    frame: pl.DataFrame,
    *,
    device: torch.device | str = "cpu",
) -> tuple[list[str], BarView]:
    """Pivot completed 1d trade/bid/ask rows into the shared wide feature contract."""
    if frame.is_empty() or frame["ticker"].n_unique() != 1:
        raise ValueError("a non-empty single-ticker daily frame is required")
    frame = frame.sort(["bar_start_us", "bar_family"])
    starts = np.unique(frame["bar_start_us"].to_numpy())
    positions = np.searchsorted(starts, frame["bar_start_us"].to_numpy())
    values = np.zeros((starts.shape[0], len(FEATURE_NAMES)), dtype=np.float32)
    family_prefix = {"trade": "trade", "quote_bid": "bid", "quote_ask": "ask"}
    unknown_families = sorted(set(str(value) for value in frame["bar_family"].to_list()) - set(family_prefix))
    if unknown_families:
        raise ValueError(f"unsupported daily bar families: {unknown_families}")
    event_counts = {family: np.zeros(starts.shape[0], dtype=np.float64) for family in family_prefix}
    for row_index, family_value in enumerate(frame["bar_family"].to_list()):
        family = str(family_value)
        prefix = family_prefix.get(family)
        if prefix is None:
            raise AssertionError(f"validated daily family unexpectedly missing: {family}")
        output_index = int(positions[row_index])
        values[output_index, FEATURE_INDEX[f"{prefix}_present"]] = 1.0
        for field in ("open", "high", "low", "close", "size_sum", "size_open", "size_high", "size_low", "size_close", "event_count"):
            values[output_index, FEATURE_INDEX[f"{prefix}_{field}"]] = float(frame[field][row_index])
        event_counts[family][output_index] = float(frame["event_count"][row_index])
    values[:, FEATURE_INDEX["source_event_count"]] = event_counts["trade"] + np.maximum(
        event_counts["quote_bid"], event_counts["quote_ask"]
    )
    grouped = frame.group_by("bar_start_us", maintain_order=True).agg(
        pl.col("local_date").first(),
        pl.col("bar_end_us").max(),
    )
    ends = grouped["bar_end_us"].to_numpy()
    dates = [str(value) for value in grouped["local_date"].to_list()]
    return dates, BarView(
        features=torch.as_tensor(values, device=device),
        bar_start_us=torch.as_tensor(np.array(starts, copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(ends, copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(ends, copy=True), dtype=torch.long, device=device),
    )


def daily_session_frame_to_view(
    frame: pl.DataFrame,
    *,
    device: torch.device | str = "cpu",
) -> tuple[list[str], BarView]:
    """Collapse three explicit SIP sessions into causally completed daily sufficient statistics."""
    if frame.is_empty() or frame["ticker"].n_unique() != 1:
        raise ValueError("a non-empty single-ticker daily-session frame is required")
    required = {"local_date", "session_kind", "bar_start_us", "bar_end_us", "available_at_us", *FEATURE_NAMES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"daily-session frame is missing columns: {missing}")
    expected_sessions = {"premarket", "regular", "after_hours"}
    unknown = sorted(set(str(value) for value in frame["session_kind"].to_list()) - expected_sessions)
    if unknown:
        raise ValueError(f"unsupported daily session kinds: {unknown}")
    frame = frame.sort(["bar_start_us", "session_kind"])
    duplicate_keys = frame.group_by(["local_date", "session_kind"]).len().filter(pl.col("len") != 1)
    if not duplicate_keys.is_empty():
        raise ValueError("daily-session frame has duplicate or ambiguous session keys")
    incomplete_dates = (
        frame.group_by("local_date")
        .agg(pl.len().alias("rows"), pl.col("session_kind").n_unique().alias("sessions"))
        .filter((pl.col("rows") != 3) | (pl.col("sessions") != 3))
    )
    if not incomplete_dates.is_empty():
        raise ValueError("daily-session frame must contain premarket, regular, and after-hours for every date")
    feature_array = np.array(frame.select(list(FEATURE_NAMES)).to_numpy(), dtype=np.float32, copy=True)
    session_view = BarView(
        features=torch.as_tensor(feature_array, device=device),
        bar_start_us=torch.as_tensor(np.array(frame["bar_start_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(frame["bar_end_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(frame["available_at_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
    )
    session_dates = [str(value) for value in frame["local_date"].to_list()]
    unique_dates = list(dict.fromkeys(session_dates))
    date_index = {value: index for index, value in enumerate(unique_dates)}
    period_ids = torch.as_tensor([date_index[value] for value in session_dates], dtype=torch.long, device=device)
    daily = rollup_calendar_view(session_view, period_ids)
    if daily.features.shape[0] != len(unique_dates):
        raise RuntimeError("daily-session collapse did not produce exactly one row per session date")
    return unique_dates, daily


def calendar_period_ids(dates: list[str], timeframe: str, *, device: torch.device | str = "cpu") -> torch.Tensor:
    import datetime as dt

    parsed = [dt.date.fromisoformat(value) for value in dates]
    if timeframe == "1W":
        ids = [day.isocalendar().year * 100 + day.isocalendar().week for day in parsed]
    elif timeframe == "1MO":
        ids = [day.year * 100 + day.month for day in parsed]
    else:
        raise ValueError("calendar timeframe must be 1W or 1MO")
    return torch.as_tensor(ids, dtype=torch.long, device=device)


def loader_contract() -> Mapping[str, object]:
    return {
        "format": "ArrowStream incremental record batches",
        "ordering": ("ticker", "local_date", "bucket_index"),
        "storage_rows": "sparse active 1s buckets",
        "model_clock": "dense 1s with explicit family availability masks",
        "coarse_views": "loader-side segment reductions",
    }


def model_features(view: BarView) -> torch.Tensor:
    return project_stationary_features(view.features)


def held_out_tickers(tickers: tuple[str, ...], fraction: float, seed: int) -> tuple[str, ...]:
    ranked = sorted(
        (str(ticker).upper() for ticker in tickers),
        key=lambda ticker: hashlib.sha256(f"{seed}:{ticker}".encode("utf-8")).digest(),
    )
    count = max(1, min(len(ranked) - 1, round(len(ranked) * float(fraction))))
    return tuple(sorted(ranked[:count]))


def _view_prefix(view: BarView, *, last_available_us: int, max_rows: int) -> BarView | None:
    count = int(torch.searchsorted(view.available_at_us, torch.tensor(last_available_us, dtype=view.available_at_us.dtype), right=True))
    if count <= 0:
        return None
    start = max(0, count - max(1, int(max_rows)))
    return BarView(
        features=view.features[start:count],
        bar_start_us=view.bar_start_us[start:count],
        bar_end_us=view.bar_end_us[start:count],
        available_at_us=view.available_at_us[start:count],
    )


def _dummy_raw() -> torch.Tensor:
    return torch.zeros((1, len(FEATURE_NAMES)), dtype=torch.float32)


def concatenate_views(left: BarView | None, right: BarView, *, left_rows: int) -> tuple[BarView, int]:
    if left is None or left.features.shape[0] == 0 or left_rows <= 0:
        return right, 0
    count = min(int(left_rows), int(left.features.shape[0]))
    if int(left.available_at_us[-1]) >= int(right.bar_start_us[0]):
        raise ValueError("prior-session halo must end before the target session starts")
    return BarView(
        features=torch.cat((left.features[-count:], right.features), dim=0),
        bar_start_us=torch.cat((left.bar_start_us[-count:], right.bar_start_us), dim=0),
        bar_end_us=torch.cat((left.bar_end_us[-count:], right.bar_end_us), dim=0),
        available_at_us=torch.cat((left.available_at_us[-count:], right.available_at_us), dim=0),
    ), count


def build_session_examples(
    *,
    ticker: str,
    local_date: str,
    session: BarView,
    daily: tuple[list[str], BarView] | None,
    split_actions: tuple[SplitAction, ...],
    config: DataConfig,
    prior_session: BarView | None = None,
    session_conditions: torch.Tensor | None = None,
    prior_conditions: torch.Tensor | None = None,
    include_incomplete_horizons: bool = False,
    origin_count_limit: int | None = None,
) -> Iterator[BarGPTExample]:
    """Yield non-overlapping origins while sharing one exact session rollup and target support."""
    context = int(config.context_bars_1s)
    right = int(config.right_support_bars_1s)
    maximum_origin = session.features.shape[0] if include_incomplete_horizons else session.features.shape[0] - right
    combined, halo_count = concatenate_views(
        prior_session if config.prior_session_halo else None,
        session,
        left_rows=context,
    )
    if session_conditions is None:
        session_conditions = torch.zeros((session.features.shape[0], 4), dtype=torch.float32)
    if session_conditions.shape != (session.features.shape[0], 4):
        raise ValueError("session conditions must align to the dense one-second session")
    if halo_count:
        if prior_conditions is None:
            prior_conditions = torch.zeros((prior_session.features.shape[0], 4), dtype=torch.float32)
        combined_conditions = torch.cat((prior_conditions[-halo_count:], session_conditions), dim=0)
    else:
        combined_conditions = session_conditions
    first_origin = max(0, context - halo_count)
    if origin_count_limit is not None:
        if int(origin_count_limit) <= 0:
            raise ValueError("origin_count_limit must be positive")
        maximum_origin = min(maximum_origin, first_origin + int(origin_count_limit))
    if maximum_origin - first_origin < int(config.min_origins_per_block):
        return
    session_anchor = int(session.available_at_us[-1])
    normalized_session = BarView(
        features=normalize_features_to_anchor(
            combined.features,
            combined.bar_start_us,
            anchor_us=session_anchor,
            actions=split_actions,
        ),
        bar_start_us=combined.bar_start_us,
        bar_end_us=combined.bar_end_us,
        available_at_us=combined.available_at_us,
    )
    calendar_views = _calendar_views(
        daily,
        anchor_us=session_anchor,
        split_actions=split_actions,
    )
    full_views: dict[str, BarView] = {"1s": normalized_session}
    scale_names = {
        5_000_000: "5s",
        30_000_000: "30s",
        60_000_000: "1m",
        300_000_000: "5m",
        900_000_000: "15m",
        3_600_000_000: "1h",
    }
    for timeframe_us in config.intraday_timeframes_us:
        if int(timeframe_us) > int(config.base_timeframe_us):
            full_views[scale_names[int(timeframe_us)]] = rollup_intraday_view(normalized_session, int(timeframe_us))
    for origin_start in range(first_origin, maximum_origin, int(config.origin_bars_1s)):
        origin_count = min(int(config.origin_bars_1s), maximum_origin - origin_start)
        if origin_count < int(config.min_origins_per_block):
            continue
        combined_origin_start = halo_count + origin_start
        input_start = combined_origin_start - context
        input_end = combined_origin_start + origin_count
        support_end = min(combined.features.shape[0], input_end + right)
        support = combined.features[input_start:support_end]
        support_conditions = combined_conditions[input_start:support_end]
        support_share_factors = cumulative_share_factors(
            combined.bar_start_us[input_start:support_end], split_actions
        ).to(combined.features.dtype)
        base_raw = normalized_session.features[input_start:input_end]
        origins = torch.arange(context, context + origin_count, dtype=torch.long)
        anchors = combined.available_at_us[input_start:input_end][origins]
        last_anchor = int(anchors[-1])
        raw_views: dict[str, torch.Tensor] = {"1s": base_raw}
        raw_view_start_us: dict[str, torch.Tensor] = {"1s": normalized_session.bar_start_us[input_start:input_end]}
        raw_view_available_at_us: dict[str, torch.Tensor] = {"1s": normalized_session.available_at_us[input_start:input_end]}
        asof: dict[str, torch.Tensor] = {}
        for name in ("5s", "30s", "1m", "5m", "15m", "1h"):
            view = full_views[name]
            duration = TIMEFRAME_US_BY_NAME[name]
            rows = max(8, context * int(config.base_timeframe_us) // duration + origin_count * int(config.base_timeframe_us) // duration + 4)
            prefix = _view_prefix(view, last_available_us=last_anchor, max_rows=rows)
            if prefix is None:
                raw_views[name] = _dummy_raw()
                raw_view_start_us[name] = torch.zeros(1, dtype=torch.long)
                raw_view_available_at_us[name] = torch.zeros(1, dtype=torch.long)
                asof[name] = torch.full((origin_count,), -1, dtype=torch.long)
            else:
                raw_views[name] = prefix.features
                raw_view_start_us[name] = prefix.bar_start_us
                raw_view_available_at_us[name] = prefix.available_at_us
                asof[name] = causal_asof_indices(prefix.available_at_us, anchors)
        for name in ("1D", "1W", "1MO"):
            view = calendar_views.get(name)
            max_rows = int(config.daily_context_bars) if name == "1D" else max(24, int(config.daily_context_bars) // (5 if name == "1W" else 21))
            prefix = _view_prefix(view, last_available_us=last_anchor, max_rows=max_rows) if view is not None else None
            if prefix is None:
                raw_views[name] = _dummy_raw()
                raw_view_start_us[name] = torch.zeros(1, dtype=torch.long)
                raw_view_available_at_us[name] = torch.zeros(1, dtype=torch.long)
                asof[name] = torch.full((origin_count,), -1, dtype=torch.long)
            else:
                raw_views[name] = prefix.features
                raw_view_start_us[name] = prefix.bar_start_us
                raw_view_available_at_us[name] = prefix.available_at_us
                asof[name] = causal_asof_indices(prefix.available_at_us, anchors)
        activity = float(base_raw[origins, FEATURE_INDEX["source_event_count"]].float().mean())
        regime = 0 if activity < config.activity_regime_low else (2 if activity >= config.activity_regime_high else 1)
        yield BarGPTExample(
            ticker=ticker,
            local_date=local_date,
            raw_views=raw_views,
            raw_view_start_us=raw_view_start_us,
            raw_view_available_at_us=raw_view_available_at_us,
            origin_indices=origins,
            origin_timestamps_us=anchors,
            asof_indices=asof,
            target_support=support,
            target_share_factors=support_share_factors,
            target_condition_flags=support_conditions,
            support_origin_indices=origins,
            horizons_us=config.horizons_us,
            base_timeframe_us=config.base_timeframe_us,
            activity_regime=regime,
        )


def _calendar_views(
    daily: tuple[list[str], BarView] | None,
    *,
    anchor_us: int,
    split_actions: tuple[SplitAction, ...],
) -> dict[str, BarView]:
    if daily is None:
        return {}
    dates, daily_view = daily
    excluded = split_execution_dates(split_actions)
    keep = torch.as_tensor([value not in excluded for value in dates], dtype=torch.bool, device=daily_view.features.device)
    filtered_dates = [value for value in dates if value not in excluded]
    if not filtered_dates:
        return {}
    normalized_daily = BarView(
        features=normalize_features_to_anchor(
            daily_view.features[keep],
            daily_view.bar_start_us[keep],
            anchor_us=anchor_us,
            actions=split_actions,
        ),
        bar_start_us=daily_view.bar_start_us[keep],
        bar_end_us=daily_view.bar_end_us[keep],
        available_at_us=daily_view.available_at_us[keep],
    )
    result = {"1D": normalized_daily}
    for name in ("1W", "1MO"):
        rolled = rollup_calendar_view(normalized_daily, calendar_period_ids(filtered_dates, name))
        # A final calendar group has no following period proving that it closed; omit it
        # rather than leaking an incomplete lifecycle week/month as a completed bar.
        if rolled.features.shape[0] > 0:
            rolled = BarView(
                rolled.features[:-1], rolled.bar_start_us[:-1], rolled.bar_end_us[:-1], rolled.available_at_us[:-1]
            )
        result[name] = rolled
    return result


def balanced_regime_stream(
    source: Iterator[BarGPTExample],
    *,
    buffer_size: int,
    seed: int,
) -> Iterator[BarGPTExample]:
    """Deterministically resample bounded buffers so available activity regimes contribute equally."""
    rng = random.Random(seed)
    while True:
        buffer: list[BarGPTExample] = []
        try:
            for _ in range(max(3, int(buffer_size))):
                buffer.append(next(source))
        except StopIteration:
            pass
        if not buffer:
            return
        by_regime = {regime: [item for item in buffer if item.activity_regime == regime] for regime in range(3)}
        active = [regime for regime, items in by_regime.items() if items]
        for items in by_regime.values():
            rng.shuffle(items)
        schedule = [active[index % len(active)] for index in range(len(buffer))]
        rng.shuffle(schedule)
        offsets = {regime: 0 for regime in active}
        for regime in schedule:
            items = by_regime[regime]
            yield items[offsets[regime] % len(items)]
            offsets[regime] += 1
        if len(buffer) < max(3, int(buffer_size)):
            return


class BarGPTSequentialDataset(Dataset[BarGPTExample]):
    """Indexable global block stream with ordered multi-worker bounded fetch.

    PyTorch assigns monotonically increasing indices across workers and its
    map-style DataLoader returns them in sampler order. Workers may therefore
    prepare future blocks concurrently without changing the trainer-visible
    ticker-month/block sequence or the single durable cursor.
    """

    def __init__(
        self,
        *,
        data_config: DataConfig,
        stream_config: ClickHouseBarStreamConfig,
        plan: SequentialBlockPlan,
        resume_cursor: CoverageCursor | None = None,
    ) -> None:
        super().__init__()
        data_config.validate()
        self.data_config = data_config
        self.stream_config = stream_config
        self.plan = plan
        self.start_global_index = plan.resume_global_index(resume_cursor)
        self._runtime: dict[str, object] | None = None

    def __getstate__(self) -> dict[str, object]:
        state = dict(self.__dict__)
        state["_runtime"] = None
        return state

    def __len__(self) -> int:
        return max(0, self.plan.total_blocks - self.start_global_index)

    def _worker_runtime(self) -> dict[str, object]:
        if self._runtime is None:
            self._runtime = {
                "client": ArrowStreamClient(self.stream_config),
                "intervals": {},
                "actions": {},
                "daily": {},
                "condition_key": None,
                "conditions": {},
                "examples": {},
            }
        return self._runtime

    def _ticker_references(
        self,
        ticker: str,
    ) -> tuple[tuple[TickerInterval, ...], tuple[SplitAction, ...], tuple[list[str], BarView] | None]:
        runtime = self._worker_runtime()
        intervals_cache = runtime["intervals"]
        actions_cache = runtime["actions"]
        daily_cache = runtime["daily"]
        assert isinstance(intervals_cache, dict)
        assert isinstance(actions_cache, dict)
        assert isinstance(daily_cache, dict)
        if ticker not in intervals_cache:
            client = runtime["client"]
            assert isinstance(client, ArrowStreamClient)
            intervals = client.read_identity_intervals(
                (ticker,),
                identity_database=self.data_config.identity_database,
                interval_table=self.data_config.identity_interval_table,
                entity_table=self.data_config.identity_entity_table,
                event_table=self.data_config.identity_event_table,
                coverage_start=self.data_config.daily_history_start_date,
            )[ticker]
            actions = client.read_split_actions(
                {ticker: intervals},
                start_date=self.data_config.daily_history_start_date,
                end_date=self.data_config.end_date,
                split_database=self.data_config.split_database,
                split_table=self.data_config.split_table,
            )[ticker]
            daily = client.read_daily_view(
                ticker=ticker,
                start_date=self.data_config.daily_history_start_date,
                end_date=self.data_config.end_date,
                daily_table=self.data_config.daily_table,
                source_intervals=intervals,
            )
            intervals_cache[ticker] = intervals
            actions_cache[ticker] = actions
            daily_cache[ticker] = daily
        return intervals_cache[ticker], actions_cache[ticker], daily_cache[ticker]

    def _unit_conditions(
        self,
        session: SequentialSessionPlan,
        intervals: tuple[TickerInterval, ...],
    ) -> dict[str, torch.Tensor]:
        runtime = self._worker_runtime()
        key = (session.ticker, session.unit_start_date, session.unit_end_date)
        if runtime["condition_key"] != key:
            client = runtime["client"]
            assert isinstance(client, ArrowStreamClient)
            fetch_start = (
                dt.date.fromisoformat(session.unit_start_date) - dt.timedelta(days=14)
            ).isoformat()
            runtime["conditions"] = client.read_condition_views(
                ticker=session.ticker,
                start_date=fetch_start,
                end_date=session.unit_end_date,
                condition_table=self.data_config.condition_table,
                source_intervals=intervals,
            )
            runtime["condition_key"] = key
        conditions = runtime["conditions"]
        assert isinstance(conditions, dict)
        return conditions

    def _chunk_indices(self, global_index: int) -> tuple[int, ...]:
        worker = get_worker_info()
        stride = (
            int(worker.num_workers) * int(self.data_config.batch_size)
            if worker is not None
            else 1
        )
        limit = max(1, int(self.data_config.origin_fetch_candidate_blocks))
        first_session, _session_block, _unit_block = self.plan.locate(global_index)
        selected: list[int] = []
        candidate = global_index
        while candidate < self.plan.total_blocks and len(selected) < limit:
            session, _local, _unit = self.plan.locate(candidate)
            if session.unit_index != first_session.unit_index:
                break
            selected.append(candidate)
            candidate += stride
        return tuple(selected)

    def _fill_examples(self, global_index: int) -> None:
        runtime = self._worker_runtime()
        client = runtime["client"]
        example_cache = runtime["examples"]
        assert isinstance(client, ArrowStreamClient)
        assert isinstance(example_cache, dict)
        indices = self._chunk_indices(global_index)
        first_session, _first_local, _first_unit = self.plan.locate(indices[0])
        intervals, actions, daily = self._ticker_references(first_session.ticker)
        conditions = self._unit_conditions(first_session, intervals)
        windows: list[OriginWindow] = []
        metadata: list[tuple[SequentialSessionPlan, int, int, int]] = []
        for index in indices:
            session, session_block, unit_block = self.plan.locate(index)
            if session.ticker != first_session.ticker or session.unit_index != first_session.unit_index:
                raise RuntimeError("one ordered fetch chunk crossed a ticker-month boundary")
            origin_start = session.first_origin + session_block * int(self.data_config.origin_bars_1s)
            origin_count = min(
                int(self.data_config.origin_bars_1s),
                SESSION_END_SECOND - SESSION_START_SECOND - origin_start,
            )
            if origin_count < int(self.data_config.min_origins_per_block):
                raise RuntimeError(f"planned block {index} has only {origin_count} origins")
            windows.append(
                OriginWindow(
                    session.local_date,
                    SESSION_START_SECOND + origin_start,
                    session.prior_date,
                    origin_count,
                )
            )
            metadata.append((session, session_block, unit_block, origin_count))
        fetched = client.read_origin_windows(
            ticker=first_session.ticker,
            windows=tuple(windows),
            source_intervals=intervals,
            context_bars=self.data_config.context_bars_1s,
            origin_bars=self.data_config.origin_bars_1s,
            right_support_bars=self.data_config.right_support_bars_1s,
        )
        for index, window, (session_view, prior_view), details in zip(
            indices, windows, fetched, metadata, strict=True
        ):
            session, _session_block, unit_block, origin_count = details
            elapsed = int(window.origin_bucket) - SESSION_START_SECOND
            target_start = max(SESSION_START_SECOND, int(window.origin_bucket) - int(self.data_config.context_bars_1s))
            target_end = min(
                SESSION_END_SECOND,
                int(window.origin_bucket) + origin_count + int(self.data_config.right_support_bars_1s),
            )
            dense_conditions = conditions.get(window.local_date)
            session_conditions = (
                dense_conditions[target_start - SESSION_START_SECOND : target_end - SESSION_START_SECOND]
                if dense_conditions is not None
                else torch.zeros((target_end - target_start, len(CONDITION_COLUMNS)), dtype=torch.float32)
            )
            prior_conditions = None
            if prior_view is not None and window.prior_date is not None:
                dense_prior = conditions.get(window.prior_date)
                prior_rows = min(int(prior_view.features.shape[0]), max(0, int(self.data_config.context_bars_1s) - elapsed))
                prior_conditions = (
                    dense_prior[-prior_rows:]
                    if dense_prior is not None
                    else torch.zeros((prior_rows, len(CONDITION_COLUMNS)), dtype=torch.float32)
                )
            built = list(
                build_session_examples(
                    ticker=session.ticker,
                    local_date=session.local_date,
                    session=session_view,
                    prior_session=prior_view,
                    session_conditions=session_conditions,
                    prior_conditions=prior_conditions,
                    daily=daily,
                    split_actions=actions,
                    config=self.data_config,
                    include_incomplete_horizons=True,
                    origin_count_limit=origin_count,
                )
            )
            if len(built) != 1 or int(built[0].origin_indices.numel()) != origin_count:
                raise RuntimeError(
                    f"global block {index} produced {len(built)} examples instead of one {origin_count}-origin example"
                )
            example = built[0]
            example.worker_id = 0  # one global durable cursor, independent of physical fetch worker
            example.unit_index = session.unit_index
            example.block_offset = unit_block
            example.session_phase = session_phase(example)
            example.has_condition_target = has_condition_target(example)
            example_cache[index] = example

    def __getitem__(self, index: int) -> BarGPTExample:
        local_index = int(index)
        if local_index < 0 or local_index >= len(self):
            raise IndexError(local_index)
        global_index = self.start_global_index + local_index
        runtime = self._worker_runtime()
        example_cache = runtime["examples"]
        assert isinstance(example_cache, dict)
        if global_index not in example_cache:
            self._fill_examples(global_index)
        example = example_cache.pop(global_index)
        assert isinstance(example, BarGPTExample)
        return example


class BarGPTIterableDataset(IterableDataset[BarGPTExample]):
    """Worker-sharded, bounded ArrowStream loader over ticker/session units."""

    def __init__(
        self,
        *,
        data_config: DataConfig,
        stream_config: ClickHouseBarStreamConfig,
        split: str,
        seed: int,
        epoch: int = 0,
        resume_cursors: Mapping[int, CoverageCursor] | None = None,
    ) -> None:
        super().__init__()
        if split not in {"train", "validation"}:
            raise ValueError("split must be train or validation")
        data_config.validate()
        self.data_config = data_config
        self.stream_config = stream_config
        self.split = split
        self.seed = int(seed)
        self._epoch = mp.Value("q", int(epoch))
        self.resume_cursors = dict(resume_cursors or {})

    @property
    def epoch(self) -> int:
        return int(self._epoch.value)

    @epoch.setter
    def epoch(self, value: int) -> None:
        with self._epoch.get_lock():
            self._epoch.value = int(value)

    def _units(self) -> list[tuple[int, TickerDateUnit]]:
        validation_tickers = {ticker for ticker, _start, _end in self.data_config.validation_slices}
        if self.split == "validation":
            selected = [TickerDateUnit(*values) for values in self.data_config.validation_slices]
        else:
            tickers = tuple(ticker for ticker in self.data_config.tickers if ticker not in validation_tickers)
            selected = month_units(
                self.data_config.start_date,
                self.data_config.validation_start_date,
                tickers,
                seed=self.seed + self.epoch,
            )
        indexed = list(enumerate(selected))
        worker = get_worker_info()
        if worker is not None:
            shards = worker_ticker_shards(
                tuple(dict.fromkeys(unit.ticker for unit in selected)),
                workers=worker.num_workers,
                seed=self.seed + self.epoch,
            )
            assigned = set(shards[worker.id])
            indexed = [(index, unit) for index, unit in indexed if unit.ticker in assigned]
        return indexed

    def __iter__(self) -> Iterator[BarGPTExample]:
        client = ArrowStreamClient(self.stream_config)
        units = self._units()
        if not units:
            return
        tickers = tuple(sorted({unit.ticker for _index, unit in units}))
        start_date = min((unit.start_date for _index, unit in units), default=self.data_config.start_date)
        end_date = max((unit.end_date for _index, unit in units), default=self.data_config.end_date)
        lookback_days = max(730, int(self.data_config.daily_context_bars * 2.2))
        daily_start = (dt.date.fromisoformat(start_date) - dt.timedelta(days=lookback_days)).isoformat()
        def raw_examples() -> Iterator[BarGPTExample]:
            intervals_by_ticker = client.read_identity_intervals(
                tickers,
                identity_database=self.data_config.identity_database,
                interval_table=self.data_config.identity_interval_table,
                entity_table=self.data_config.identity_entity_table,
                event_table=self.data_config.identity_event_table,
                coverage_start=daily_start,
            )
            actions_by_ticker = client.read_split_actions(
                intervals_by_ticker,
                start_date=daily_start,
                end_date=end_date,
                split_database=self.data_config.split_database,
                split_table=self.data_config.split_table,
            )
            daily_by_ticker: dict[str, tuple[list[str], BarView] | None] = {
                ticker: value
                for ticker, value in client.read_daily_views(
                    tickers=tickers,
                    start_date=daily_start,
                    end_date=end_date,
                    daily_table=self.data_config.daily_table,
                    intervals_by_ticker=intervals_by_ticker,
                ).items()
            }
            for ticker in tickers:
                daily_by_ticker.setdefault(ticker, None)
            worker = get_worker_info()
            worker_id = worker.id if worker is not None else 0
            resume = self.resume_cursors.get(worker_id)
            for unit_index, unit in units:
                if resume is not None and unit_index < resume.unit_index:
                    continue
                ticker = unit.ticker
                split_actions = actions_by_ticker.get(ticker, ())
                excluded_dates = split_execution_dates(split_actions)
                fetch_start = (dt.date.fromisoformat(unit.start_date) - dt.timedelta(days=14)).isoformat()
                conditions_by_date = client.read_condition_views(
                    ticker=ticker,
                    start_date=fetch_start,
                    end_date=unit.end_date,
                    condition_table=self.data_config.condition_table,
                    source_intervals=intervals_by_ticker[ticker],
                )
                if self.split == "train" and self.data_config.coverage_mode == "sequential":
                    # Fetch ordered sparse bars in bounded date pages.  Each session is
                    # densified once, then split into consecutive O-origin examples; the
                    # preceding session remains the raw causal halo for the next session.
                    previous_session: BarView | None = None
                    previous_conditions: torch.Tensor | None = None
                    emitted = 0
                    for local_date, session in client.iter_session_views(
                        ticker=ticker,
                        start_date=fetch_start,
                        end_date=unit.end_date,
                        source_intervals=intervals_by_ticker[ticker],
                    ):
                        dense_conditions = conditions_by_date.get(local_date)
                        if dense_conditions is None:
                            dense_conditions = torch.zeros((session.features.shape[0], 4), dtype=torch.float32)
                        if local_date < unit.start_date:
                            previous_session = session
                            previous_conditions = dense_conditions
                            continue
                        for example in build_session_examples(
                            ticker=ticker,
                            local_date=local_date,
                            session=session,
                            prior_session=previous_session,
                            session_conditions=dense_conditions,
                            prior_conditions=previous_conditions,
                            daily=daily_by_ticker.get(ticker),
                            split_actions=split_actions,
                            config=self.data_config,
                            include_incomplete_horizons=True,
                        ):
                            block_offset = emitted
                            emitted += 1
                            if (
                                resume is not None
                                and unit_index == resume.unit_index
                                and block_offset <= resume.block_offset
                            ):
                                continue
                            example.worker_id = worker_id
                            example.unit_index = unit_index
                            example.block_offset = block_offset
                            example.session_phase = session_phase(example)
                            example.has_condition_target = has_condition_target(example)
                            yield example
                        previous_session = session
                        previous_conditions = dense_conditions
                    continue
                limit = (
                    self.data_config.validation_blocks_per_slice
                    if self.split == "validation"
                    else self.data_config.coverage_blocks_per_unit
                )
                daily_value = daily_by_ticker.get(ticker)
                daily_dates = daily_value[0] if daily_value is not None else []
                fetch_blocks = int(self.data_config.origin_fetch_candidate_blocks)
                emit_blocks = int(self.data_config.origin_emit_blocks_per_chunk)
                candidate_count = math.ceil(limit / emit_blocks) * fetch_blocks
                schedule = origin_window_schedule(
                    dates=[day for day in daily_dates if day not in excluded_dates],
                    start_date=unit.start_date,
                    end_date=unit.end_date,
                    count=candidate_count,
                    context_bars=self.data_config.context_bars_1s,
                    origin_bars=self.data_config.origin_bars_1s,
                    right_support_bars=self.data_config.right_support_bars_1s,
                    conditions_by_date=conditions_by_date,
                    seed=self.seed + self.epoch * 1_000_003 + unit_index,
                )
                emitted = 0
                for chunk_index in range(0, len(schedule), fetch_blocks):
                    if emitted >= limit:
                        break
                    windows = schedule[chunk_index : chunk_index + fetch_blocks]
                    fetched = client.read_origin_windows(
                        ticker=ticker,
                        windows=windows,
                        source_intervals=intervals_by_ticker[ticker],
                        context_bars=self.data_config.context_bars_1s,
                        origin_bars=self.data_config.origin_bars_1s,
                        right_support_bars=self.data_config.right_support_bars_1s,
                    )
                    candidates: list[BarGPTExample] = []
                    for window, (session, prior_session) in zip(windows, fetched, strict=True):
                        target_start = max(
                            SESSION_START_SECOND,
                            int(window.origin_bucket) - int(self.data_config.context_bars_1s),
                        )
                        target_end = (
                            int(window.origin_bucket)
                            + int(self.data_config.origin_bars_1s)
                            + int(self.data_config.right_support_bars_1s)
                        )
                        dense_conditions = conditions_by_date.get(window.local_date)
                        session_conditions = (
                            dense_conditions[
                                target_start - SESSION_START_SECOND : target_end - SESSION_START_SECOND
                            ]
                            if dense_conditions is not None
                            else torch.zeros((target_end - target_start, 4), dtype=torch.float32)
                        )
                        prior_conditions = None
                        if prior_session is not None and window.prior_date is not None:
                            dense_prior = conditions_by_date.get(window.prior_date)
                            prior_rows = int(prior_session.features.shape[0])
                            prior_conditions = (
                                dense_prior[-prior_rows:]
                                if dense_prior is not None
                                else torch.zeros((prior_rows, 4), dtype=torch.float32)
                            )
                        built = list(
                            build_session_examples(
                                ticker=ticker,
                                local_date=window.local_date,
                                session=session,
                                prior_session=prior_session,
                                session_conditions=session_conditions,
                                prior_conditions=prior_conditions,
                                daily=daily_value,
                                split_actions=split_actions,
                                config=self.data_config,
                            )
                        )
                        if len(built) > 1:
                            raise RuntimeError("one bounded origin window produced multiple training blocks")
                        candidates.extend(built)
                    selected = select_stratified_examples(
                        candidates,
                        limit=min(emit_blocks, limit - emitted),
                        seed=self.seed + self.epoch * 1_000_003 + unit_index * 4099 + chunk_index,
                        balance_activity_regimes=self.data_config.balance_activity_regimes,
                    ) if candidates else []
                    for example in selected:
                        block_offset = emitted
                        emitted += 1
                        if (
                            resume is not None
                            and unit_index == resume.unit_index
                            and block_offset <= resume.block_offset
                        ):
                            continue
                        example.worker_id = worker_id
                        example.unit_index = unit_index
                        example.block_offset = block_offset
                        yield example
        yield from raw_examples()


def make_dataloader(
    dataset: BarGPTIterableDataset,
    config: DataConfig,
    *,
    drop_last: bool,
) -> DataLoader[BarGPTExample]:
    kwargs: dict[str, object] = {}
    if config.loader_workers > 0:
        kwargs["prefetch_factor"] = int(config.worker_prefetch_batches)
        kwargs["persistent_workers"] = bool(config.persistent_workers)
        # Preserve each worker's local order for cursor safety, but never let a
        # slow ClickHouse query hide ready batches produced by other workers.
        kwargs["in_order"] = False
    return DataLoader(
        dataset,
        batch_size=int(config.batch_size),
        num_workers=int(config.loader_workers),
        pin_memory=bool(config.pin_memory),
        drop_last=drop_last,
        collate_fn=partial(collate_examples, balance_activity_regimes=config.balance_activity_regimes),
        **kwargs,
    )


def make_sequential_dataloader(
    dataset: BarGPTSequentialDataset,
    config: DataConfig,
) -> DataLoader[BarGPTExample]:
    """Parallel fetch with strict sampler-order emission."""
    kwargs: dict[str, object] = {}
    if config.loader_workers > 0:
        kwargs["prefetch_factor"] = int(config.worker_prefetch_batches)
        kwargs["persistent_workers"] = bool(config.persistent_workers)
        kwargs["in_order"] = True
    return DataLoader(
        dataset,
        batch_size=int(config.batch_size),
        shuffle=False,
        num_workers=int(config.loader_workers),
        pin_memory=bool(config.pin_memory),
        drop_last=False,
        collate_fn=partial(collate_examples, balance_activity_regimes=config.balance_activity_regimes),
        **kwargs,
    )


def timeframe_contract() -> tuple[dict[str, int], dict[str, int]]:
    return dict(TIMEFRAME_US_BY_NAME), dict(PATHWAY_ID_BY_NAME)
