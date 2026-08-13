from __future__ import annotations

import datetime as dt
import math
import re
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar
from zoneinfo import ZoneInfo

import polars as pl
import torch
from torch.utils.data import get_worker_info

from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import (
    condition_event_select_sql,
    condition_token_array_aliases_sql,
)
from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.data import (
    BarGPTExample,
    BarView,
    FixedBucketHistoryCache,
    TIMEFRAME_US_BY_NAME,
    rollup_calendar_view,
)
from research.bar_gpt.v2.loader import (
    ArrowStreamClient,
    BarGPTIterableDataset,
    CONDITION_COUNT_COLUMNS,
    TickerDateUnit,
    TickerInterval,
    _history_satisfies_intraday_context,
    build_session_examples,
    frame_to_sparse_view,
    split_execution_dates,
)
from research.bar_gpt.v2.sampling import has_condition_target, session_phase
from research.bar_gpt.v2.schema import (
    FEATURE_INDEX,
    FEATURE_SPECS,
    FEATURE_NAMES,
    ONE_SECOND_US,
    SESSION_END_SECOND,
    SESSION_START_SECOND,
    SESSION_TIMEZONE,
)
from research.mlops.clickhouse import quote_ident, sql_string


DIRECT_EVENT_SOURCE_VERSION = "bar_gpt_direct_events_trade_sparse_v3"

PageValue = TypeVar("PageValue")


@dataclass(frozen=True, slots=True)
class DirectEventSession:
    """One price-sparse session plus its independent wall-clock conditions."""

    local_date: str
    view: BarView | None
    condition_flags: torch.Tensor
    trailing_condition_counts: torch.Tensor


_CONDITION_STORAGE_COLUMNS = (
    *CONDITION_COUNT_COLUMNS,
    "condition_nonzero_count",
    "condition_event_count",
    "source_event_count",
)


def _session_from_direct_rows(
    part: pl.DataFrame,
    *,
    device: torch.device | str,
) -> DirectEventSession:
    """Separate price eligibility from condition availability without row loops.

    Every condition second remains at its original timestamp for physical
    targets. For price-view input features, conditions are folded into the
    first subsequent trade-bearing token because there is no model origin
    between the condition and that token. Conditions after the final trade are
    returned as a carry for the next session.
    """
    part = part.sort("bar_start_us")
    local_date = str(part["local_date"][0])
    day = dt.date.fromisoformat(local_date)
    midnight = dt.datetime.combine(day, dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE))
    session_start_us = int(
        (midnight + dt.timedelta(seconds=SESSION_START_SECOND)).timestamp() * 1_000_000
    )
    condition_rows = torch.as_tensor(
        part.select(CONDITION_COUNT_COLUMNS).to_numpy(), dtype=torch.float32
    )
    condition_indices = torch.as_tensor(
        ((part["bar_start_us"].to_numpy().copy() - session_start_us) // ONE_SECOND_US),
        dtype=torch.long,
    )
    condition_flags = torch.zeros(
        (SESSION_END_SECOND - SESSION_START_SECOND, len(CONDITION_COUNT_COLUMNS)),
        dtype=torch.float32,
    )
    valid_clock = (
        (condition_indices >= 0)
        & (condition_indices < condition_flags.shape[0])
        & (condition_rows.sum(dim=-1) > 0)
    )
    if bool(valid_clock.any()):
        condition_flags.index_add_(
            0,
            condition_indices[valid_clock],
            (condition_rows[valid_clock] > 0).to(condition_flags.dtype),
        )
        condition_flags.clamp_(max=1)

    price_part = part.filter(pl.col("origin_eligible") > 0)
    trailing = torch.zeros(len(_CONDITION_STORAGE_COLUMNS), dtype=torch.float64)
    if price_part.height == 0:
        trailing = torch.as_tensor(
            part.select(_CONDITION_STORAGE_COLUMNS).sum().row(0), dtype=torch.float64
        )
        return DirectEventSession(local_date, None, condition_flags.to(device), trailing)

    view = frame_to_sparse_view(price_part, device=device)
    condition_available = torch.as_tensor(part["available_at_us"].to_numpy().copy(), dtype=torch.long)
    positions = torch.searchsorted(view.available_at_us.cpu(), condition_available, right=False)
    storage_values = torch.as_tensor(
        part.select(_CONDITION_STORAGE_COLUMNS).to_numpy().copy(), dtype=torch.float64
    )
    mapped = torch.zeros(
        (view.features.shape[0], len(_CONDITION_STORAGE_COLUMNS)), dtype=torch.float64
    )
    has_next_price = positions < view.features.shape[0]
    if bool(has_next_price.any()):
        mapped.index_add_(0, positions[has_next_price], storage_values[has_next_price])
    if bool((~has_next_price).any()):
        trailing = storage_values[~has_next_price].sum(dim=0)
    for column_index, name in enumerate(_CONDITION_STORAGE_COLUMNS):
        view.features[:, FEATURE_INDEX[name]] = mapped[:, column_index].to(
            device=view.features.device, dtype=view.features.dtype
        )
    return DirectEventSession(local_date, view, condition_flags.to(device), trailing)


def _apply_condition_carry(session: DirectEventSession, carry: torch.Tensor) -> BarView | None:
    if session.view is None:
        return None
    if not bool((carry > 0).any()):
        return session.view
    features = session.view.features.clone()
    for column_index, name in enumerate(_CONDITION_STORAGE_COLUMNS):
        features[0, FEATURE_INDEX[name]] += carry[column_index].to(
            device=features.device, dtype=features.dtype
        )
    return BarView(
        features=features,
        bar_start_us=session.view.bar_start_us,
        bar_end_us=session.view.bar_end_us,
        available_at_us=session.view.available_at_us,
    )


def _chain_condition_sessions(
    sessions: list[DirectEventSession],
    *,
    initial_carry: torch.Tensor | None = None,
) -> tuple[list[tuple[DirectEventSession, BarView]], torch.Tensor]:
    carry = (
        torch.zeros(len(_CONDITION_STORAGE_COLUMNS), dtype=torch.float64)
        if initial_carry is None
        else initial_carry.clone().to(dtype=torch.float64, device="cpu")
    )
    resolved: list[tuple[DirectEventSession, BarView]] = []
    for session in sessions:
        view = _apply_condition_carry(session, carry)
        if view is None:
            carry += session.trailing_condition_counts.cpu()
            continue
        resolved.append((session, view))
        carry = session.trailing_condition_counts.clone().cpu()
    return resolved, carry


def _iter_prefetched_pages_in_order(
    pages: deque[tuple[dt.date, dt.date]],
    *,
    depth: int,
    read_page: Callable[[dt.date, dt.date], list[PageValue]],
    page_callback: Callable[[str, str, int, float, int, int, float], None] | None,
    thread_name_prefix: str,
) -> Iterator[list[PageValue]]:
    """Keep query slots full and report completions without reordering data.

    ClickHouse partitions can finish out of order. Waiting on the oldest future
    hid completed work from the terminal and left newly free query slots idle.
    Results are buffered by page ordinal so callers still receive strict
    chronological input, while callbacks and replacement submissions happen as
    soon as any partition finishes.
    """
    pending_pages = deque(enumerate(pages))
    pending: dict[Future[list[PageValue]], tuple[int, dt.date, dt.date, float]] = {}
    ready: dict[int, list[PageValue]] = {}
    next_ordinal = 0
    completed_pages = 0
    total_pages = len(pending_pages)
    phase_started = time.perf_counter()

    if page_callback is not None:
        page_callback("", "", 0, 0.0, 0, total_pages, 0.0)

    with ThreadPoolExecutor(
        max_workers=max(1, int(depth)), thread_name_prefix=thread_name_prefix
    ) as executor:
        def submit_available() -> None:
            while pending_pages and len(pending) < max(1, int(depth)):
                ordinal, (left, right) = pending_pages.popleft()
                future = executor.submit(read_page, left, right)
                pending[future] = (ordinal, left, right, time.perf_counter())

        submit_available()
        while pending:
            completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in completed:
                ordinal, left, right, started = pending.pop(future)
                page = future.result()
                ready[ordinal] = page
                completed_pages += 1
                if page_callback is not None:
                    page_callback(
                        left.isoformat(), right.isoformat(), len(page),
                        time.perf_counter() - started, completed_pages, total_pages,
                        time.perf_counter() - phase_started,
                    )
            submit_available()
            while next_ordinal in ready:
                yield ready.pop(next_ordinal)
                next_ordinal += 1


def calendar_lookback_days(config: DataConfig) -> int:
    """Convert the configured trading-day warm-up to a conservative calendar span."""
    return max(366, math.ceil(int(config.calendar_warmup_daily_bars) * 1.5))


def _is_event_authority_boundary(config: DataConfig, unit_start_date: str) -> bool:
    """Return whether no earlier source history can exist by contract."""
    return str(unit_start_date) == str(config.daily_history_start_date)


def direct_event_preflight(client: object, config: DataConfig, tickers: tuple[str, ...]) -> tuple[dict[str, object], dict[str, int]]:
    """Validate immutable event inputs and obtain non-scanning scheduler weights."""
    lookback_days = calendar_lookback_days(config)
    source_start = max(
        config.daily_history_start_date,
        (dt.date.fromisoformat(config.start_date) - dt.timedelta(days=lookback_days)).isoformat(),
    )
    years = range(dt.date.fromisoformat(source_start).year, (dt.date.fromisoformat(config.end_date) - dt.timedelta(days=1)).year + 1)
    required = [f"{config.events_table_base}_{year}" for year in years]
    required.extend(("events_ticker_day_index", "events_source_day_stats", config.condition_reference_table))
    selected = ",".join(sql_string(value) for value in required)
    rows = client.execute(
        f"SELECT name FROM system.tables WHERE database={sql_string(config.database)} AND name IN ({selected}) FORMAT TSV"
    )
    present = {line.strip() for line in rows.splitlines() if line.strip()}
    missing = sorted(set(required) - present)
    if missing:
        raise RuntimeError(f"direct event source authorities are missing: {missing}")
    provenance = client.execute(
        f"""
SELECT groupUniqArray(source_filter_key)
FROM {quote_ident(config.database)}.events_source_day_stats FINAL
WHERE source_date>=toDate({sql_string(source_start)}) AND source_date<toDate({sql_string(config.end_date)})
FORMAT TSV
"""
    ).strip()
    required_filter = "drop_trade_correction_codes=07,08,10,11|condition_slots=5"
    if required_filter not in provenance:
        raise RuntimeError(
            "direct event authority provenance is incompatible: "
            f"required {required_filter!r}, observed {provenance!r}"
        )
    ticker_sql = ",".join(sql_string(ticker) for ticker in tickers)
    weight_rows = client.execute(
        f"""
SELECT upper(ticker),sum(event_count)
FROM {quote_ident(config.database)}.events_ticker_day_index FINAL
WHERE source_date>=toDate({sql_string(config.start_date)}) AND source_date<toDate({sql_string(config.end_date)})
  AND upper(ticker) IN ({ticker_sql})
GROUP BY ticker
FORMAT TSV
"""
    ) if tickers else ""
    observed_weights = {
        fields[0].upper(): int(fields[1])
        for line in weight_rows.splitlines()
        if len(fields := line.split("\t")) == 2
    }
    # Scheduler weights are estimates, not coverage authority.  A requested
    # ticker can legitimately have no rows in the selected interval (for
    # example, a later IPO in a historical build), so retain it explicitly
    # with zero weight instead of returning a partial mapping.
    weights = {
        ticker.upper(): max(0, int(observed_weights.get(ticker.upper(), 0)))
        for ticker in tickers
    }
    return {
        "mode": "direct_events",
        "version": DIRECT_EVENT_SOURCE_VERSION,
        "source_start": source_start,
        "source_end": config.end_date,
        "required_tables": required,
        "source_filter": required_filter,
        "trade_required_for_every_token": True,
    }, weights


def _event_source(database: str, table_base: str, start_date: str, end_date: str) -> str:
    left = dt.date.fromisoformat(start_date)
    right = dt.date.fromisoformat(end_date) - dt.timedelta(days=1)
    years = tuple(range(left.year, right.year + 1))
    if len(years) == 1:
        return f"{quote_ident(database)}.{quote_ident(f'{table_base}_{years[0]}')}"
    pattern = "^(" + "|".join(re.escape(f"{table_base}_{year}") for year in years) + ")$"
    return f"merge({sql_string(database)}, {sql_string(pattern)})"


def _ohlc(prefix: str, value: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toFloat32(argMinIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_open')}",
        f"toFloat32(maxIf({value}, {condition})) AS {quote_ident(prefix + '_high')}",
        f"toFloat32(minIf({value}, {condition})) AS {quote_ident(prefix + '_low')}",
        f"toFloat32(argMaxIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_close')}",
    ]


def _family_aggregates(prefix: str, price: str, size: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toUInt8(countIf({condition}) > 0) AS {quote_ident(prefix + '_present')}",
        *_ohlc(prefix, price, condition),
        f"toFloat64(sumIf({size}, {condition})) AS {quote_ident(prefix + '_size_sum')}",
        f"toFloat64(argMinIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_open')}",
        f"toFloat64(maxIf({size}, {condition})) AS {quote_ident(prefix + '_size_high')}",
        f"toFloat64(minIf({size}, {condition})) AS {quote_ident(prefix + '_size_low')}",
        f"toFloat64(argMaxIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_close')}",
        f"toFloat64(sumIf({size} * {size}, {condition})) AS {quote_ident(prefix + '_size_squared_sum')}",
        f"toFloat64(sumIf({price} * {size}, {condition})) AS {quote_ident(prefix + '_price_size_sum')}",
        f"toUInt64(countIf({condition})) AS {quote_ident(prefix + '_event_count')}",
    ]


def _trade_aggregates() -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        "toUInt8(countIf(trade_origin_eligible) > 0) AS trade_present",
        f"toFloat32(argMinIf(trade_price, {order}, trade_origin_eligible)) AS trade_open",
        "toFloat32(maxIf(trade_price, trade_high_low_eligible)) AS trade_high",
        "toFloat32(minIf(trade_price, trade_high_low_eligible)) AS trade_low",
        f"toFloat32(argMaxIf(trade_price, {order}, trade_last_eligible)) AS trade_close",
        "toFloat64(sumIf(trade_size, trade_volume_eligible)) AS trade_size_sum",
        f"toFloat64(argMinIf(trade_size, {order}, trade_volume_eligible)) AS trade_size_open",
        "toFloat64(maxIf(trade_size, trade_volume_eligible)) AS trade_size_high",
        "toFloat64(minIf(trade_size, trade_volume_eligible)) AS trade_size_low",
        f"toFloat64(argMaxIf(trade_size, {order}, trade_volume_eligible)) AS trade_size_close",
        "toFloat64(sumIf(trade_size * trade_size, trade_volume_eligible)) AS trade_size_squared_sum",
        "toFloat64(sumIf(trade_price * trade_size, trade_volume_eligible AND trade_origin_eligible)) AS trade_price_size_sum",
        "toUInt64(countIf(trade_volume_eligible)) AS trade_event_count",
    ]


def _relation_aggregates(prefix: str, value: str, condition: str) -> list[str]:
    return [
        *_ohlc(prefix, value, condition),
        f"toFloat64(sumIf({value}, {condition})) AS {quote_ident(prefix + '_sum')}",
        f"toFloat64(sumIf({value} * {value}, {condition})) AS {quote_ident(prefix + '_squared_sum')}",
    ]


def direct_trade_bar_query(
    config: DataConfig,
    stream_config: object,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    source_intervals: tuple[TickerInterval, ...],
    group_daily: bool = False,
    _finalize: bool = True,
) -> str:
    """Build trade-bearing 1s or daily sufficient-statistic rows from events."""
    if group_daily:
        inner = direct_trade_bar_query(
            config,
            stream_config,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            source_intervals=source_intervals,
            _finalize=False,
        )
        rolled: list[str] = []
        for spec in FEATURE_SPECS:
            column = quote_ident(spec.name)
            source_column = f"s.{column}"
            valid = "1" if spec.validity == "always" else f"s.{quote_ident(spec.validity)} != 0"
            if spec.reducer == "sum":
                expression = f"sum({source_column})"
            elif spec.reducer == "max":
                expression = f"maxIf({source_column}, {valid})"
            elif spec.reducer == "min":
                expression = f"minIf({source_column}, {valid})"
            elif spec.reducer == "first":
                expression = f"argMinIf({source_column}, s.bar_start_us, {valid})"
            elif spec.reducer == "last":
                expression = f"argMaxIf({source_column}, s.bar_start_us, {valid})"
            else:
                raise ValueError(f"unsupported feature reducer: {spec.reducer}")
            rolled.append(f"{expression} AS {column}")
        selected_rollup = ",\n    ".join(rolled)
        return f"""
SELECT
    s.local_date,
    any(s.ticker) AS ticker,
    min(s.bar_start_us) AS bar_start_us,
    max(s.bar_end_us) AS bar_end_us,
    max(s.available_at_us) AS available_at_us,
    {selected_rollup},
    toUInt64(sum(s.origin_eligible)) AS eligible_trade_second_count
FROM
(
{inner}
) AS s
GROUP BY s.local_date
ORDER BY local_date
SETTINGS max_threads={max(1, int(stream_config.max_threads))}, max_block_size={max(1, int(stream_config.max_block_size))}, max_memory_usage={max(1, int(stream_config.max_memory_usage))}
FORMAT ArrowStream
"""
    predicates: list[str] = []
    for interval in source_intervals:
        left = max(start_date, interval.valid_from)
        right = min(end_date, interval.valid_to_exclusive)
        if left < right:
            predicates.append(
                f"(e.ticker={sql_string(interval.source_ticker)} "
                f"AND e.event_date>=toDate({sql_string(left)}) "
                f"AND e.event_date<toDate({sql_string(right)}))"
            )
    if not predicates:
        raise ValueError(f"no point-in-time event interval covers {ticker} in [{start_date},{end_date})")
    condition_args = type("ConditionArgs", (), {
        "database": config.database,
        "condition_token_reference_table": config.condition_reference_table,
    })()
    condition_aliases = condition_token_array_aliases_sql(condition_args)
    condition_events = condition_event_select_sql()
    pair_valid = "quote_origin_eligible"
    aggregates = [
        *_trade_aggregates(),
        *_family_aggregates("bid", "bid_price", "bid_size", pair_valid),
        *_family_aggregates("ask", "ask_price", "ask_size", pair_valid),
        f"toUInt8(countIf({pair_valid}) > 0) AS quote_pair_present",
        f"toUInt64(countIf({pair_valid})) AS quote_pair_count",
        *_relation_aggregates("spread", "spread", pair_valid),
        *_relation_aggregates("midpoint", "midpoint", pair_valid),
        *_relation_aggregates("microprice", "microprice", pair_valid),
        *_relation_aggregates("queue_imbalance", "queue_imbalance", pair_valid),
        "toUInt64(countIf(quote_origin_eligible AND bid_price = ask_price)) AS locked_quote_count",
        "toUInt64(countIf(quote_origin_eligible AND bid_price > ask_price)) AS crossed_quote_count",
        "toUInt64(sumIf(toUInt8(condition_token_1 > 0) + toUInt8(condition_token_2 > 0) + toUInt8(condition_token_3 > 0) + toUInt8(condition_token_4 > 0) + toUInt8(condition_token_5 > 0), event_retained OR condition_halt_pause_flag_event > 0 OR condition_resume_flag_event > 0 OR condition_news_risk_flag_event > 0 OR condition_luld_limit_state_flag_event > 0)) AS condition_nonzero_count",
        "toUInt64(countIf(event_retained OR condition_halt_pause_flag_event > 0 OR condition_resume_flag_event > 0 OR condition_news_risk_flag_event > 0 OR condition_luld_limit_state_flag_event > 0)) AS source_event_count",
        "toFloat64(sumIf(trade_size, trade_volume_eligible AND trade_origin_eligible)) AS trade_price_eligible_size_sum",
        "toUInt8(countIf(trade_origin_eligible) > 0) AS context_eligible",
        "toUInt8(countIf(trade_origin_eligible) > 0) AS origin_eligible",
        "toUInt64(countIf(trade_origin_eligible)) AS origin_event_count",
        "toUInt64(countIf(trade_origin_eligible)) AS eligible_trade_event_count",
        "toUInt64(countIf(quote_origin_eligible)) AS eligible_quote_event_count",
        "toUInt64(countIf(event_type = 1 AND NOT event_retained)) AS rejected_trade_event_count",
        "toUInt64(countIf(event_type = 0 AND NOT quote_origin_eligible)) AS rejected_quote_event_count",
        "toUInt64(countIf(trade_condition_unknown OR quote_condition_unknown)) AS unknown_condition_event_count",
        # Status conditions are a separate causal authority. A halt/LULD row
        # may deliberately have no usable trade or quote; price eligibility
        # must never erase the status event.
        "toUInt64(countIf(condition_halt_pause_flag_event > 0)) AS condition_halt_pause_count",
        "toUInt64(countIf(condition_resume_flag_event > 0)) AS condition_resume_count",
        "toUInt64(countIf(condition_news_risk_flag_event > 0)) AS condition_news_risk_count",
        "toUInt64(countIf(condition_luld_limit_state_flag_event > 0)) AS condition_luld_limit_state_count",
        "toUInt64(countIf(condition_halt_pause_flag_event > 0 OR condition_resume_flag_event > 0 OR condition_news_risk_flag_event > 0 OR condition_luld_limit_state_flag_event > 0)) AS condition_event_count",
    ]
    source = _event_source(config.database, config.events_table_base, start_date, end_date)
    selected = ",\n    ".join((
        "local_date_value AS local_date",
        f"{sql_string(ticker.upper())} AS ticker",
        "second_start_us AS bar_start_us",
        f"second_start_us + toUInt64({ONE_SECOND_US}) AS bar_end_us",
        f"second_start_us + toUInt64({ONE_SECOND_US}) AS available_at_us",
        *aggregates,
    ))
    final_clause = (
        f"ORDER BY local_date, bar_start_us\n"
        f"SETTINGS max_threads={max(1, int(stream_config.max_threads))}, "
        f"max_block_size={max(1, int(stream_config.max_block_size))}, "
        f"max_memory_usage={max(1, int(stream_config.max_memory_usage))}, optimize_read_in_order=1\n"
        "FORMAT ArrowStream"
        if _finalize else ""
    )
    return f"""
WITH
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='trade_conditions' AND is_join_canonical=1 AND update_last=1) AS update_last_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='trade_conditions' AND is_join_canonical=1 AND update_high_low=1) AS update_high_low_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='trade_conditions' AND is_join_canonical=1 AND update_volume=1) AS update_volume_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='trade_conditions' AND is_join_canonical=1) AS all_trade_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='quote_conditions' AND is_join_canonical=1) AS all_quote_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='trade_conditions' AND is_join_canonical=1 AND modifier_int=12) AS trade_model_ineligible_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {quote_ident(config.database)}.{quote_ident(config.condition_reference_table)} WHERE source_family='quote_conditions' AND is_join_canonical=1 AND modifier_int IN (-1,12,15,19,20,80,83,84)) AS quote_origin_ineligible_tokens,
    {condition_aliases},
    toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    toDate(ts_local) AS local_date_value,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second,
    bitAnd(event_meta, 1) AS event_type,
    toFloat64(if(price_primary_int>0, price_primary_int / if(bitAnd(event_meta,2)=2,10000.0,100.0), 0.0)) AS primary_price,
    toFloat64(if(price_secondary_int>0, price_secondary_int / if(bitAnd(event_meta,4)=4,10000.0,100.0), 0.0)) AS secondary_price,
    if(event_type=1,primary_price,0.0) AS trade_price,
    if(event_type=1,toFloat64(size_primary),0.0) AS trade_size,
    if(event_type=0,primary_price,0.0) AS ask_price,
    if(event_type=0,secondary_price,0.0) AS bid_price,
    if(event_type=0,toFloat64(size_primary),0.0) AS ask_size,
    if(event_type=0,toFloat64(size_secondary),0.0) AS bid_size,
    ask_price-bid_price AS spread,
    (ask_price+bid_price)/2.0 AS midpoint,
    if(ask_size+bid_size>0,(ask_price*bid_size+bid_price*ask_size)/(ask_size+bid_size),0.0) AS microprice,
    if(ask_size+bid_size>0,(bid_size-ask_size)/(bid_size+ask_size),0.0) AS queue_imbalance,
    arrayFilter(token -> token != 0, [condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) AS condition_tokens,
    {condition_events},
    arrayFilter(token -> token != 0, [condition_token_1,condition_token_2,condition_token_3,condition_token_4]) AS quote_condition_tokens,
    event_type=1 AND trade_price>0 AND trade_size>0 AS trade_structurally_valid,
    event_type=1 AND arrayExists(token -> has(trade_model_ineligible_tokens,token),condition_tokens) AS trade_model_ineligible,
    trade_structurally_valid AND NOT trade_model_ineligible AND notEmpty(condition_tokens) AND arrayAll(token -> has(update_last_tokens,token),condition_tokens) AS trade_last_eligible,
    trade_structurally_valid AND NOT trade_model_ineligible AND notEmpty(condition_tokens) AND arrayAll(token -> has(update_high_low_tokens,token),condition_tokens) AS trade_high_low_eligible,
    trade_structurally_valid AND NOT trade_model_ineligible AND notEmpty(condition_tokens) AND arrayAll(token -> has(update_volume_tokens,token),condition_tokens) AS trade_volume_eligible,
    trade_structurally_valid AND (empty(condition_tokens) OR arrayExists(token -> NOT has(all_trade_tokens,token),condition_tokens)) AS trade_condition_unknown,
    trade_last_eligible OR trade_high_low_eligible AS trade_origin_eligible,
    event_type=0 AND bid_price>0 AND ask_price>0 AND bid_size>0 AND ask_size>0 AND bid_price<=ask_price AS quote_structurally_valid,
    if(quote_structurally_valid,(ask_price-bid_price)/((ask_price+bid_price)/2.0)*10000.0,1e100) AS quote_spread_bps,
    quote_structurally_valid AND notEmpty(quote_condition_tokens) AND quote_spread_bps<={float(config.max_quote_spread_bps):.12g} AND arrayAll(token -> NOT has(quote_origin_ineligible_tokens,token),quote_condition_tokens) AS quote_origin_eligible,
    quote_structurally_valid AND (empty(quote_condition_tokens) OR arrayExists(token -> NOT has(all_quote_tokens,token),quote_condition_tokens)) AS quote_condition_unknown,
    trade_origin_eligible OR trade_volume_eligible OR quote_origin_eligible AS event_retained,
    intDiv(toUInt64(sip_timestamp_us),toUInt64({ONE_SECOND_US}))*toUInt64({ONE_SECOND_US}) AS second_start_us
SELECT
    {selected}
FROM {source} AS e
PREWHERE {' OR '.join(predicates)}
WHERE local_second>={SESSION_START_SECOND} AND local_second<{SESSION_END_SECOND}
GROUP BY local_date_value, second_start_us
HAVING eligible_trade_event_count>0 OR condition_event_count>0
{final_clause}
"""


def daily_bar_from_session(local_date: str, session: BarView) -> tuple[list[str], BarView]:
    """Collapse one completed trade-bearing session without fabricating empty days."""
    period_ids = torch.zeros(session.features.shape[0], dtype=torch.long, device=session.features.device)
    daily = rollup_calendar_view(session, period_ids)
    day = dt.date.fromisoformat(local_date)
    midnight = dt.datetime.combine(day, dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE))
    start = int((midnight + dt.timedelta(seconds=SESSION_START_SECOND)).timestamp() * 1_000_000)
    end = int((midnight + dt.timedelta(seconds=SESSION_END_SECOND)).timestamp() * 1_000_000)
    daily.bar_start_us[:] = start
    daily.bar_end_us[:] = end
    daily.available_at_us[:] = end
    return [local_date], daily


def append_daily(
    current: tuple[list[str], BarView] | None,
    incoming: tuple[list[str], BarView],
    *,
    max_rows: int,
) -> tuple[list[str], BarView]:
    dates, view = incoming
    if current is not None:
        left_dates, left = current
        dates = [*left_dates, *dates]
        view = BarView(
            features=torch.cat((left.features, view.features)),
            bar_start_us=torch.cat((left.bar_start_us, view.bar_start_us)),
            bar_end_us=torch.cat((left.bar_end_us, view.bar_end_us)),
            available_at_us=torch.cat((left.available_at_us, view.available_at_us)),
        )
    keep = min(max(1, int(max_rows)), len(dates))
    return dates[-keep:], BarView(
        features=view.features[-keep:],
        bar_start_us=view.bar_start_us[-keep:],
        bar_end_us=view.bar_end_us[-keep:],
        available_at_us=view.available_at_us[-keep:],
    )


class DirectEventArrowStreamClient(ArrowStreamClient):
    def __init__(self, stream_config: object, data_config: DataConfig, *, query_gate: object | None = None) -> None:
        super().__init__(stream_config, query_gate=query_gate)
        self.data_config = data_config

    def iter_session_bundles(self, *, ticker: str, start_date: str, end_date: str,
                             source_intervals: tuple[TickerInterval, ...] = (), device: torch.device | str = "cpu",
                             prefetch_pages: int = 1,
                             page_callback: Callable[[str, str, int, float, int, int, float], None] | None = None,
                             ) -> Iterator[DirectEventSession]:
        def read_page(left: dt.date, right: dt.date) -> list[DirectEventSession]:
            started = time.perf_counter()
            query = direct_trade_bar_query(
                self.data_config, self.config, ticker=ticker,
                start_date=left.isoformat(), end_date=right.isoformat(),
                source_intervals=source_intervals,
            )
            frames: list[pl.DataFrame] = []
            with self.record_batches(query) as batches:
                frames.extend(pl.from_arrow(batch) for batch in batches if batch.num_rows)
            result: list[DirectEventSession] = []
            if frames:
                frame = pl.concat(frames, how="vertical")
                for part in frame.partition_by("local_date", maintain_order=True):
                    result.append(_session_from_direct_rows(part, device=device))
            self._add_timing("direct_event_page_seconds", time.perf_counter() - started)
            return result

        cursor = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        pages: deque[tuple[dt.date, dt.date]] = deque()
        page_start = cursor
        while page_start < end:
            # events_YYYY is partitioned by calendar month. Query each monthly
            # partition once instead of repeatedly rescanning it in seven-day
            # windows; the first and last ranges remain bounded by the request.
            next_month = (page_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            page_end = min(end, next_month)
            pages.append((page_start, page_end))
            page_start = page_end
        for page in _iter_prefetched_pages_in_order(
            pages,
            depth=prefetch_pages,
            read_page=read_page,
            page_callback=page_callback,
            thread_name_prefix="bar-gpt-direct-page",
        ):
            yield from page

    def iter_session_views(self, *, ticker: str, start_date: str, end_date: str,
                           source_intervals: tuple[TickerInterval, ...] = (), device: torch.device | str = "cpu",
                           prefetch_pages: int = 1,
                           page_callback: Callable[[str, str, int, float, int, int, float], None] | None = None,
                           ) -> Iterator[tuple[str, BarView]]:
        """Compatibility projection for read-only source audits."""
        bundles = list(self.iter_session_bundles(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            source_intervals=source_intervals,
            device=device,
            prefetch_pages=prefetch_pages,
            page_callback=page_callback,
        ))
        resolved, _carry = _chain_condition_sessions(bundles)
        for session, view in resolved:
            yield session.local_date, view

    def iter_daily_views(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        source_intervals: tuple[TickerInterval, ...] = (),
        device: torch.device | str = "cpu",
        prefetch_pages: int = 1,
        page_callback: Callable[[str, str, int, float, int, int, float], None] | None = None,
    ) -> Iterator[tuple[str, BarView, int]]:
        """Aggregate calendar warm-up directly to daily rows inside ClickHouse."""
        def read_page(left: dt.date, right: dt.date) -> list[tuple[str, BarView, int]]:
            started = time.perf_counter()
            query = direct_trade_bar_query(
                self.data_config,
                self.config,
                ticker=ticker,
                start_date=left.isoformat(),
                end_date=right.isoformat(),
                source_intervals=source_intervals,
                group_daily=True,
            )
            frames: list[pl.DataFrame] = []
            with self.record_batches(query) as batches:
                frames.extend(pl.from_arrow(batch) for batch in batches if batch.num_rows)
            result: list[tuple[str, BarView, int]] = []
            if frames:
                frame = pl.concat(frames, how="vertical")
                for part in frame.partition_by("local_date", maintain_order=True):
                    day = str(part["local_date"][0])
                    view = frame_to_sparse_view(part, device=device)
                    midnight = dt.datetime.combine(
                        dt.date.fromisoformat(day), dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE)
                    )
                    session_start = int(
                        (midnight + dt.timedelta(seconds=SESSION_START_SECOND)).timestamp() * 1_000_000
                    )
                    session_end = int(
                        (midnight + dt.timedelta(seconds=SESSION_END_SECOND)).timestamp() * 1_000_000
                    )
                    view.bar_start_us[:] = session_start
                    view.bar_end_us[:] = session_end
                    view.available_at_us[:] = session_end
                    result.append((day, view, int(part["eligible_trade_second_count"][0])))
            self._add_timing("direct_daily_page_seconds", time.perf_counter() - started)
            return result

        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        pages: deque[tuple[dt.date, dt.date]] = deque()
        page_start = start
        while page_start < end:
            next_month = (page_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            page_end = min(end, next_month)
            pages.append((page_start, page_end))
            page_start = page_end
        carry = torch.zeros(len(_CONDITION_STORAGE_COLUMNS), dtype=torch.float64)
        for page in _iter_prefetched_pages_in_order(
            pages,
            depth=prefetch_pages,
            read_page=read_page,
            page_callback=page_callback,
            thread_name_prefix="bar-gpt-direct-daily",
        ):
            for day, view, eligible_seconds in page:
                if eligible_seconds <= 0:
                    carry += torch.stack(tuple(
                        view.features[0, FEATURE_INDEX[name]].double().cpu()
                        for name in _CONDITION_STORAGE_COLUMNS
                    ))
                    continue
                if bool((carry > 0).any()):
                    features = view.features.clone()
                    for column_index, name in enumerate(_CONDITION_STORAGE_COLUMNS):
                        features[0, FEATURE_INDEX[name]] += carry[column_index].to(
                            device=features.device, dtype=features.dtype
                        )
                    view = BarView(
                        features=features,
                        bar_start_us=view.bar_start_us,
                        bar_end_us=view.bar_end_us,
                        available_at_us=view.available_at_us,
                    )
                    carry.zero_()
                yield day, view, eligible_seconds


class DirectEventShardDataset(BarGPTIterableDataset):
    """Single-pass event-to-shard dataset with ticker-owned rolling state."""

    def __init__(
        self,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        emit_start_date: str | None = None,
        **kwargs: object,
    ) -> None:
        # A direct-event build is stateful across ticker-months. Certified
        # units must be replayed as state-only inputs on resume rather than
        # removed from the dataset, or the first missing unit enters through a
        # numerically different server-side calendar warm-up path.
        skipped = frozenset(str(value) for value in kwargs.pop("skip_unit_keys", frozenset()))
        kwargs["skip_unit_keys"] = frozenset()
        super().__init__(**kwargs)
        self.progress_callback = progress_callback
        self.state_only_unit_keys = skipped
        self.emit_start_date = (
            dt.date.fromisoformat(str(emit_start_date)).isoformat()
            if emit_start_date is not None
            else None
        )

    def _units(self) -> list[tuple[int, TickerDateUnit]]:
        units = super()._units()
        if not self.state_only_unit_keys:
            return units
        emitting = [
            index
            for index, (_global_index, unit) in enumerate(units)
            if f"{unit.ticker}:{unit.start_date[:7]}" not in self.state_only_unit_keys
        ]
        if not emitting:
            return []
        # Earlier certified units establish rolling state. Certified units
        # after the final missing unit have no downstream consumer this run.
        return units[: emitting[-1] + 1]

    def _page_progress(
        self,
        phase: str,
        left: str,
        right: str,
        sessions: int,
        seconds: float,
        completed: int,
        total: int,
        elapsed: float,
    ) -> None:
        if self.progress_callback is not None:
            self.progress_callback({
                "kind": "page",
                "phase": phase,
                "left": left,
                "right": right,
                "sessions": int(sessions),
                "query_seconds": float(seconds),
                "completed": int(completed),
                "total": int(total),
                "elapsed_seconds": float(elapsed),
            })

    def _stage_progress(self, phase: str, detail: str = "") -> None:
        if self.progress_callback is not None:
            self.progress_callback({
                "kind": "stage",
                "phase": phase,
                "detail": detail,
                "completed": 0,
                "total": 0,
            })

    def __iter__(self) -> Iterator[BarGPTExample]:
        if self.split != "cache" or self.data_config.coverage_mode != "sequential":
            raise RuntimeError("direct event shard compilation supports sequential cache coverage only")
        units = self._units()
        if not units:
            return
        client = DirectEventArrowStreamClient(self.stream_config, self.data_config, query_gate=self.query_gate)
        tickers = tuple(sorted({unit.ticker for _index, unit in units}))
        first_start = min(unit.start_date for _index, unit in units)
        final_end = max(unit.end_date for _index, unit in units)
        lookback_days = calendar_lookback_days(self.data_config)
        metadata_start = max(
            self.data_config.daily_history_start_date,
            (dt.date.fromisoformat(first_start) - dt.timedelta(days=lookback_days)).isoformat(),
        )
        self._stage_progress("identity metadata", f"{len(tickers)} ticker(s)")
        intervals = client.read_identity_intervals(
            tickers,
            identity_database=self.data_config.identity_database,
            interval_table=self.data_config.identity_interval_table,
            entity_table=self.data_config.identity_entity_table,
            event_table=self.data_config.identity_event_table,
            coverage_start=metadata_start,
        )
        self._stage_progress("split metadata", f"{len(tickers)} ticker(s)")
        actions = client.read_split_actions(
            intervals,
            start_date=metadata_start,
            end_date=final_end,
            split_database=self.data_config.split_database,
            split_table=self.data_config.split_table,
        )
        worker = get_worker_info()
        worker_id = worker.id if worker is not None else 0
        active_ticker = ""
        history: FixedBucketHistoryCache | None = None
        daily: tuple[list[str], BarView] | None = None
        loaded_through = ""
        condition_carry = torch.zeros(len(_CONDITION_STORAGE_COLUMNS), dtype=torch.float64)
        emitted_by_unit: dict[str, int] = {}

        for unit_index, unit in units:
            current_unit_key = f"{unit.ticker}:{unit.start_date[:7]}"
            if active_ticker != unit.ticker:
                active_ticker = unit.ticker
                history = FixedBucketHistoryCache(max_rows=max(
                    int(self.data_config.intraday_warmup_bars_1s), int(self.data_config.context_bars_1s)
                ))
                daily = None
                loaded_through = ""
                condition_carry.zero_()
            assert history is not None
            split_actions = actions.get(unit.ticker, ())
            excluded_daily = split_execution_dates(split_actions)
            if not loaded_through:
                warmup_start = max(
                    self.data_config.daily_history_start_date,
                    (dt.date.fromisoformat(unit.start_date) - dt.timedelta(days=lookback_days)).isoformat(),
                )
                eligible_seconds_by_day: list[tuple[str, int]] = []
                self._stage_progress("calendar warmup", f"{unit.ticker} planning source partitions")
                for day, daily_view, eligible_seconds in client.iter_daily_views(
                    ticker=unit.ticker,
                    start_date=warmup_start,
                    end_date=unit.start_date,
                    source_intervals=intervals[unit.ticker],
                    prefetch_pages=self.data_config.clickhouse_prefetch_pages,
                    page_callback=lambda left, right, sessions, seconds, completed, total, elapsed: self._page_progress(
                        "calendar warmup", left, right, sessions, seconds, completed, total, elapsed
                    ),
                ):
                    eligible_seconds_by_day.append((day, eligible_seconds))
                    if day not in excluded_daily:
                        daily = append_daily(
                            daily,
                            ([day], daily_view),
                            max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32,
                        )
                # Sparse context readiness is defined independently for every
                # model view.  The 28,800-row value is only the worst-case
                # source-buffer capacity needed to cover eight physical hours
                # when every second contains an event; it is not a required
                # count of sparse context tokens.
                required_one_second = int(self.data_config.intraday_context_by_name["1s"])
                accumulated = 0
                intraday_warmup_start = warmup_start
                selected_day_index = len(eligible_seconds_by_day)
                self._stage_progress(
                    "intraday warmup planning",
                    f"{unit.ticker}: resolving each configured view independently",
                )
                for index in range(len(eligible_seconds_by_day) - 1, -1, -1):
                    day, eligible_seconds = eligible_seconds_by_day[index]
                    accumulated += int(eligible_seconds)
                    intraday_warmup_start = day
                    selected_day_index = index
                    if accumulated >= required_one_second:
                        break
                # Include one preceding trade-bearing session so a status event
                # after its final trade can be carried into the first retained
                # price token without inventing a condition-only token.
                if eligible_seconds_by_day and selected_day_index > 0:
                    intraday_warmup_start = eligible_seconds_by_day[selected_day_index - 1][0]
                    selected_day_index -= 1
                warmup_sessions: list[DirectEventSession] = []
                self._stage_progress("intraday warmup", f"{unit.ticker} planning source partitions")
                for session in client.iter_session_bundles(
                    ticker=unit.ticker, start_date=intraday_warmup_start, end_date=unit.start_date,
                    source_intervals=intervals[unit.ticker], prefetch_pages=self.data_config.clickhouse_prefetch_pages,
                    page_callback=lambda left, right, sessions, seconds, completed, total, elapsed: self._page_progress(
                        "intraday warmup", left, right, sessions, seconds, completed, total, elapsed
                    ),
                ):
                    warmup_sessions.append(session)

                def rebuild_intraday_history() -> None:
                    nonlocal history, condition_carry
                    history = FixedBucketHistoryCache(max_rows=max(
                        int(self.data_config.intraday_warmup_bars_1s),
                        int(self.data_config.context_bars_1s),
                    ))
                    resolved, condition_carry = _chain_condition_sessions(warmup_sessions)
                    for _bundle, warmup_view in resolved:
                        history.append(warmup_view, materialize=False)

                rebuild_intraday_history()
                # Daily counts select a small initial range efficiently. If an
                # illiquid/security-specific distribution still lacks one of
                # the configured coarse views, expand backward in bounded
                # monthly-sized session batches and certify the actual rollups.
                while (
                    not _history_satisfies_intraday_context(history.view, self.data_config)
                    and selected_day_index > 0
                ):
                    previous_index = selected_day_index
                    selected_day_index = max(0, selected_day_index - 22)
                    extension_start = eligible_seconds_by_day[selected_day_index][0]
                    extension_end = eligible_seconds_by_day[previous_index][0]
                    older_sessions = [
                        session for session in client.iter_session_bundles(
                            ticker=unit.ticker,
                            start_date=extension_start,
                            end_date=extension_end,
                            source_intervals=intervals[unit.ticker],
                            prefetch_pages=self.data_config.clickhouse_prefetch_pages,
                            page_callback=lambda left, right, sessions, seconds, completed, total, elapsed: self._page_progress(
                                "intraday warmup extension", left, right, sessions, seconds,
                                completed, total, elapsed,
                            ),
                        )
                    ]
                    warmup_sessions = older_sessions + warmup_sessions
                    rebuild_intraday_history()

                if not _history_satisfies_intraday_context(history.view, self.data_config):
                    if not _is_event_authority_boundary(self.data_config, unit.start_date):
                        raise RuntimeError(
                            f"{unit.ticker}: available sparse history before {unit.start_date} does not satisfy "
                            f"configured intraday contexts {self.data_config.intraday_context_by_name}"
                        )
                    # The event authority begins at the requested first unit.
                    # Fixed context slots are retained, but unavailable history
                    # is zero-filled and masked while real sparse bars accrue.
                    self._stage_progress(
                        "authority boundary",
                        f"{unit.ticker}: accumulating per-view context from {unit.start_date}; "
                        "early origins will use explicit missing-history masks",
                    )
                loaded_through = unit.start_date
            fetch_start = loaded_through
            emitted = emitted_by_unit.get(f"{unit.ticker}:{unit.start_date[:7]}", 0)
            self._stage_progress("build source", f"{unit.ticker}:{unit.start_date[:7]}")
            for bundle in client.iter_session_bundles(
                ticker=unit.ticker, start_date=fetch_start, end_date=unit.end_date,
                source_intervals=intervals[unit.ticker], prefetch_pages=self.data_config.clickhouse_prefetch_pages,
                page_callback=lambda left, right, sessions, seconds, completed, total, elapsed: self._page_progress(
                    "build", left, right, sessions, seconds, completed, total, elapsed
                ),
            ):
                day = bundle.local_date
                session = _apply_condition_carry(bundle, condition_carry)
                if session is None:
                    condition_carry += bundle.trailing_condition_counts.cpu()
                    continue
                condition_carry = bundle.trailing_condition_counts.clone().cpu()
                if day < unit.start_date:
                    history.append(session, materialize=False)
                    if day not in excluded_daily:
                        daily = append_daily(daily, daily_bar_from_session(day, session), max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32)
                    continue
                # Exact reconstruction audits replay the frozen catalog from
                # its original start so rolling calendar statistics follow the
                # same numerical path as the one-pass builder.  Retain that
                # state without compiling or yielding unrelated earlier units.
                if (
                    current_unit_key in self.state_only_unit_keys
                    or (self.emit_start_date is not None and day < self.emit_start_date)
                ):
                    history.append(session, materialize=False)
                    if day not in excluded_daily:
                        daily = append_daily(
                            daily,
                            daily_bar_from_session(day, session),
                            max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32,
                        )
                    continue
                for example in build_session_examples(
                    ticker=unit.ticker, local_date=day, session=session,
                    prior_session=history.view, daily=daily, split_actions=split_actions,
                    config=self.data_config, include_incomplete_horizons=True,
                    session_conditions=bundle.condition_flags,
                ):
                    example.loader_stage_seconds = client.take_timings()
                    example.worker_id = worker_id
                    example.unit_index = unit_index
                    example.block_offset = emitted
                    example.session_phase = session_phase(example)
                    example.has_condition_target = has_condition_target(example)
                    emitted += 1
                    yield example
                history.append(session, materialize=False)
                if day not in excluded_daily:
                    daily = append_daily(daily, daily_bar_from_session(day, session), max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32)
            emitted_by_unit[f"{unit.ticker}:{unit.start_date[:7]}"] = emitted
            loaded_through = unit.end_date
