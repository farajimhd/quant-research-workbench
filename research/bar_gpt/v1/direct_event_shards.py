from __future__ import annotations

import datetime as dt
import math
import re
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Iterator
from zoneinfo import ZoneInfo

import polars as pl
import torch
from torch.utils.data import get_worker_info

from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import (
    condition_event_select_sql,
    condition_token_array_aliases_sql,
)
from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.data import (
    BarGPTExample,
    BarView,
    FixedBucketHistoryCache,
    TIMEFRAME_US_BY_NAME,
    rollup_calendar_view,
)
from research.bar_gpt.v1.loader import (
    ArrowStreamClient,
    BarGPTIterableDataset,
    TickerInterval,
    _history_satisfies_intraday_context,
    build_session_examples,
    frame_to_sparse_view,
    split_execution_dates,
)
from research.bar_gpt.v1.sampling import has_condition_target, session_phase
from research.bar_gpt.v1.schema import (
    FEATURE_NAMES,
    ONE_SECOND_US,
    SESSION_END_SECOND,
    SESSION_START_SECOND,
    SESSION_TIMEZONE,
)
from research.mlops.clickhouse import quote_ident, sql_string


DIRECT_EVENT_SOURCE_VERSION = "bar_gpt_direct_events_trade_sparse_v1"


def direct_event_preflight(client: object, config: DataConfig, tickers: tuple[str, ...]) -> tuple[dict[str, object], dict[str, int]]:
    """Validate immutable event inputs and obtain non-scanning scheduler weights."""
    lookback_days = max(730, math.ceil(config.calendar_warmup_daily_bars * 2.2))
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
    weights = {
        fields[0].upper(): int(fields[1])
        for line in weight_rows.splitlines()
        if len(fields := line.split("\t")) == 2
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
) -> str:
    """Build sparse trade-bearing 1s rows directly from compact events."""
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
        "toUInt64(sumIf(toUInt8(condition_token_1 > 0) + toUInt8(condition_token_2 > 0) + toUInt8(condition_token_3 > 0) + toUInt8(condition_token_4 > 0) + toUInt8(condition_token_5 > 0), event_retained)) AS condition_nonzero_count",
        "toUInt64(countIf(event_retained)) AS source_event_count",
        "toFloat64(sumIf(trade_size, trade_volume_eligible AND trade_origin_eligible)) AS trade_price_eligible_size_sum",
        "toUInt8(countIf(trade_origin_eligible) > 0) AS context_eligible",
        "toUInt8(countIf(trade_origin_eligible) > 0) AS origin_eligible",
        "toUInt64(countIf(trade_origin_eligible)) AS origin_event_count",
        "toUInt64(countIf(trade_origin_eligible)) AS eligible_trade_event_count",
        "toUInt64(countIf(quote_origin_eligible)) AS eligible_quote_event_count",
        "toUInt64(countIf(event_type = 1 AND NOT event_retained)) AS rejected_trade_event_count",
        "toUInt64(countIf(event_type = 0 AND NOT quote_origin_eligible)) AS rejected_quote_event_count",
        "toUInt64(countIf(trade_condition_unknown OR quote_condition_unknown)) AS unknown_condition_event_count",
        "toUInt64(countIf(event_retained AND condition_halt_pause_flag_event > 0)) AS condition_halt_pause_count",
        "toUInt64(countIf(event_retained AND condition_resume_flag_event > 0)) AS condition_resume_count",
        "toUInt64(countIf(event_retained AND condition_news_risk_flag_event > 0)) AS condition_news_risk_count",
        "toUInt64(countIf(event_retained AND condition_luld_limit_state_flag_event > 0)) AS condition_luld_limit_state_count",
        "toUInt64(countIf(event_retained AND (condition_halt_pause_flag_event > 0 OR condition_resume_flag_event > 0 OR condition_news_risk_flag_event > 0 OR condition_luld_limit_state_flag_event > 0))) AS condition_event_count",
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
HAVING eligible_trade_event_count>0
ORDER BY local_date, bar_start_us
SETTINGS max_threads={max(1, int(stream_config.max_threads))}, max_block_size={max(1, int(stream_config.max_block_size))}, max_memory_usage={max(1, int(stream_config.max_memory_usage))}, optimize_read_in_order=1
FORMAT ArrowStream
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

    def iter_session_views(self, *, ticker: str, start_date: str, end_date: str,
                           source_intervals: tuple[TickerInterval, ...] = (), device: torch.device | str = "cpu",
                           prefetch_pages: int = 1) -> Iterator[tuple[str, BarView]]:
        def read_page(left: dt.date, right: dt.date) -> list[tuple[str, BarView]]:
            started = time.perf_counter()
            query = direct_trade_bar_query(
                self.data_config, self.config, ticker=ticker,
                start_date=left.isoformat(), end_date=right.isoformat(),
                source_intervals=source_intervals,
            )
            frames: list[pl.DataFrame] = []
            with self.record_batches(query) as batches:
                frames.extend(pl.from_arrow(batch) for batch in batches if batch.num_rows)
            result: list[tuple[str, BarView]] = []
            if frames:
                frame = pl.concat(frames, how="vertical")
                for part in frame.partition_by("local_date", maintain_order=True):
                    day = str(part["local_date"][0])
                    result.append((day, frame_to_sparse_view(part, device=device)))
            self._add_timing("direct_event_page_seconds", time.perf_counter() - started)
            return result

        cursor = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        depth = max(1, int(prefetch_pages))
        with ThreadPoolExecutor(max_workers=depth, thread_name_prefix="bar-gpt-direct-page") as executor:
            pending: deque[tuple[dt.date, Future[list[tuple[str, BarView]]]]] = deque()
            submit = cursor
            while submit < end and len(pending) < depth:
                right = min(end, submit + dt.timedelta(days=max(1, int(self.config.query_days))))
                pending.append((right, executor.submit(read_page, submit, right)))
                submit = right
            while pending:
                right, future = pending.popleft()
                page = future.result()
                if submit < end:
                    next_right = min(end, submit + dt.timedelta(days=max(1, int(self.config.query_days))))
                    pending.append((next_right, executor.submit(read_page, submit, next_right)))
                    submit = next_right
                yield from page
                cursor = right


class DirectEventShardDataset(BarGPTIterableDataset):
    """Single-pass event-to-shard dataset with ticker-owned rolling state."""

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
        lookback_days = max(730, math.ceil(self.data_config.calendar_warmup_daily_bars * 2.2))
        metadata_start = max(
            self.data_config.daily_history_start_date,
            (dt.date.fromisoformat(first_start) - dt.timedelta(days=lookback_days)).isoformat(),
        )
        intervals = client.read_identity_intervals(
            tickers,
            identity_database=self.data_config.identity_database,
            interval_table=self.data_config.identity_interval_table,
            entity_table=self.data_config.identity_entity_table,
            event_table=self.data_config.identity_event_table,
            coverage_start=metadata_start,
        )
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
        emitted_by_unit: dict[str, int] = {}

        for unit_index, unit in units:
            if active_ticker != unit.ticker:
                active_ticker = unit.ticker
                history = FixedBucketHistoryCache(max_rows=max(
                    int(self.data_config.intraday_warmup_bars_1s), int(self.data_config.context_bars_1s)
                ))
                daily = None
                loaded_through = ""
            assert history is not None
            split_actions = actions.get(unit.ticker, ())
            excluded_daily = split_execution_dates(split_actions)
            if not loaded_through:
                warmup_start = max(
                    self.data_config.daily_history_start_date,
                    (dt.date.fromisoformat(unit.start_date) - dt.timedelta(days=lookback_days)).isoformat(),
                )
                for day, session in client.iter_session_views(
                    ticker=unit.ticker, start_date=warmup_start, end_date=unit.start_date,
                    source_intervals=intervals[unit.ticker], prefetch_pages=self.data_config.clickhouse_prefetch_pages,
                ):
                    history.append(session, materialize=False)
                    if day not in excluded_daily:
                        daily = append_daily(
                            daily, daily_bar_from_session(day, session),
                            max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32,
                        )
                loaded_through = unit.start_date
            fetch_start = loaded_through
            emitted = emitted_by_unit.get(f"{unit.ticker}:{unit.start_date[:7]}", 0)
            for day, session in client.iter_session_views(
                ticker=unit.ticker, start_date=fetch_start, end_date=unit.end_date,
                source_intervals=intervals[unit.ticker], prefetch_pages=self.data_config.clickhouse_prefetch_pages,
            ):
                if day < unit.start_date:
                    history.append(session, materialize=False)
                    if day not in excluded_daily:
                        daily = append_daily(daily, daily_bar_from_session(day, session), max_rows=int(self.data_config.calendar_warmup_daily_bars) + 32)
                    continue
                for example in build_session_examples(
                    ticker=unit.ticker, local_date=day, session=session,
                    prior_session=history.view, daily=daily, split_actions=split_actions,
                    config=self.data_config, include_incomplete_horizons=True,
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
