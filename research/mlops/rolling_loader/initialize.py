from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.loader import RollingContextLoader
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler
from research.mlops.rolling_loader.sources import (
    ClickHouseExternalContextSource,
    ClickHouseRollingSource,
    RollingTickerIndexRow,
)


@dataclass(frozen=True, slots=True)
class InitializedRollingReplay:
    """A guide-aligned initialized replay state.

    The loader has already created per-ticker caches for the full universe,
    loaded high-frequency carryover rows, loaded bounded low-frequency/global
    context as-of the replay start, and positioned per-ticker event cursors.
    """

    source: ClickHouseRollingSource
    context_source: ClickHouseExternalContextSource
    cursors: dict[str, int]
    index_rows: tuple[RollingTickerIndexRow, ...]
    initialized_tickers: tuple[str, ...]
    warm_tickers: int
    warm_rows: int
    initial_context_updates: int
    start_timestamp_us: int
    source_summary: dict[str, object]


def initialize_clickhouse_replay(
    *,
    loader: RollingContextLoader,
    loader_config: RollingLoaderConfig,
    source: ClickHouseRollingSource,
    context_source: ClickHouseExternalContextSource,
    profiler: RollingLoaderProfiler,
    ticker_limit: int,
    events_per_ticker_block: int,
    start_timestamp_us: int = 0,
) -> InitializedRollingReplay:
    """Initialize all rolling caches before sample replay begins.

    If ``start_timestamp_us`` is provided, warm high-frequency rows end at the
    latest event visible at that timestamp for each ticker. If it is omitted,
    the profiler-compatible fallback warms from the start of the configured
    index table.
    """

    warm_count = int(loader_config.warmup_events_per_ticker)
    min_events = warm_count + max(1, int(events_per_ticker_block))
    with profiler.stage("source_load_ticker_index", items=int(ticker_limit)):
        index_rows = tuple(source.load_ticker_index_rows(limit=int(ticker_limit), min_events=min_events))
    if not index_rows:
        raise RuntimeError(f"No eligible tickers found for min_events={min_events:,}")

    start_us = int(start_timestamp_us)

    if start_us > 0:
        with profiler.stage("source_resolve_start_ordinals", items=len(index_rows)):
            start_ordinals = source.load_start_ordinals(index_rows=index_rows, start_timestamp_us=start_us)
        if not start_ordinals:
            raise RuntimeError(f"No ticker has events at or before start_timestamp_us={start_us}")
        available_index_rows = tuple(row for row in index_rows if row.ticker in start_ordinals)
        initialized_tickers = loader.initialize_universe(row.ticker for row in available_index_rows)
        with profiler.stage("warm_load_source_rows", items=len(start_ordinals)):
            warm_rows_by_ticker = source.warm_rows_ending_at(
                index_rows=available_index_rows,
                end_ordinals=start_ordinals,
                warm_count=warm_count,
                asof_timestamp_us=start_us,
            )
        cursors = source.initial_cursors_from_ordinals(end_ordinals={ticker: start_ordinals[ticker] for ticker in initialized_tickers})
        initial_context_asof_us = start_us
    else:
        initialized_tickers = loader.initialize_universe(row.ticker for row in index_rows)
        with profiler.stage("warm_load_source_rows", items=len(index_rows)):
            warm_rows_by_ticker = source.warm_rows_from_index(index_rows=index_rows, warm_count=warm_count)
        cursors = source.initial_cursors_from_index(index_rows=index_rows, warm_count=warm_count)
        initial_context_asof_us = _initial_context_asof_timestamp_us(warm_rows_by_ticker)

    loader.warm_load_events(warm_rows_by_ticker)
    initial_updates = []
    if initial_context_asof_us > 0:
        with profiler.stage("warm_load_external_context_fetch", items=len(index_rows)):
            initial_updates = context_source.load_initial_context_asof(
                tickers=initialized_tickers,
                asof_timestamp_us=initial_context_asof_us,
            )
        with profiler.stage(
            "warm_load_external_context_apply",
            items=len(initial_updates),
            bytes_count=sum(update.payload.nbytes for update in initial_updates),
        ):
            for update in initial_updates:
                loader.push_external(
                    kind=update.kind,
                    ticker=update.ticker,
                    timestamp_us=update.timestamp_us,
                    payload=update.payload,
                    global_item=update.global_item,
                )

    warm_rows = sum(int(rows.shape[0]) for rows in warm_rows_by_ticker.values())
    return InitializedRollingReplay(
        source=source,
        context_source=context_source,
        cursors=cursors,
        index_rows=index_rows,
        initialized_tickers=initialized_tickers,
        warm_tickers=len(warm_rows_by_ticker),
        warm_rows=warm_rows,
        initial_context_updates=len(initial_updates),
        start_timestamp_us=initial_context_asof_us,
        source_summary={
            "source": "clickhouse",
            "ticker_limit": int(ticker_limit),
            "tickers_initialized": len(initialized_tickers),
            "tickers_loaded": len(index_rows),
            "tickers_warmed": len(warm_rows_by_ticker),
            "warm_count": warm_count,
            "warm_rows": warm_rows,
            "start_timestamp_us": initial_context_asof_us,
            "initial_context_updates": len(initial_updates),
            "min_events": min_events,
        },
    )


def _initial_context_asof_timestamp_us(rows_by_ticker: dict[str, object]) -> int:
    values = []
    for rows in rows_by_ticker.values():
        if getattr(rows, "size", 0):
            values.append(int(rows["sip_timestamp_us"][-1]))
    return min(values) if values else 0


def initialized_ticker_set(index_rows: Iterable[RollingTickerIndexRow]) -> tuple[str, ...]:
    return tuple(sorted({row.ticker for row in index_rows}))
