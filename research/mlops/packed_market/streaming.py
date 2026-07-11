from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any, Iterable, Iterator, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import numpy as np
import polars as pl

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.packed_market.cache import PackedBlockManifest, PackedMarketBlock, utc_now_iso

SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 3600
SESSION_REGULAR_START_SECOND = 9 * 3600 + 30 * 60
SESSION_REGULAR_END_SECOND = 16 * 3600
SESSION_END_SECOND = 20 * 3600
SESSION_LENGTH_SECOND = SESSION_END_SECOND - SESSION_START_SECOND

STREAM_EVENT_FEATURE_NAMES = (
    "event_meta",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)

DEFAULT_HORIZONS_US = {
    "100ms": 100_000,
    "200ms": 200_000,
    "500ms": 500_000,
    "1s": 1_000_000,
    "2s": 2_000_000,
    "5s": 5_000_000,
    "10s": 10_000_000,
    "30s": 30_000_000,
    "60s": 60_000_000,
    "300s": 300_000_000,
}


@dataclass(frozen=True, slots=True)
class TickerMonthPlan:
    plan_index: int
    ticker: str
    month: str
    event_count: int
    first_origin_ordinal: int
    last_origin_ordinal: int
    first_timestamp_us: int
    last_timestamp_us: int


@dataclass(frozen=True, slots=True)
class TickerBlockJob:
    plan: TickerMonthPlan
    block_id: int
    origin_start_ordinal: int
    origin_end_ordinal: int
    fetch_start_ordinal: int
    fetch_end_ordinal: int


@dataclass(slots=True)
class ClickHouseTickerStreamConfig:
    months: tuple[str, ...] = ("2019-02",)
    tickers: tuple[str, ...] = ()
    database: str = "market_sip_compact"
    events_table_base: str = "events"
    events_ticker_day_index_table: str = "events_ticker_day_index"
    clickhouse_url: str = field(default_factory=default_clickhouse_url)
    user: str = field(default_factory=default_clickhouse_user)
    password: str = field(default_factory=default_clickhouse_password)
    ticker_workers: int = 24
    ready_queue_blocks: int = 8
    target_origin_count_per_block: int = 65_536
    event_context_rows: int = 1_024
    future_event_guard_rows: int = 262_144
    max_blocks: int = 0
    max_plans: int = 0
    max_threads_per_query: int = 4
    max_memory_usage: str = "32G"
    query_retries: int = 2
    query_retry_backoff_seconds: float = 1.0
    worker_memory_limit_mib: int = 12_288
    shuffle_plans: bool = False
    seed: int = 17
    label_horizons_us: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_HORIZONS_US))


@dataclass(slots=True)
class WorkerSlot:
    worker_id: int
    status: str = "idle"
    ticker: str = ""
    month: str = ""
    current_job: str = ""
    event_rows: int = 0
    origin_rows: int = 0
    fetch_seconds: float = 0.0
    process_seconds: float = 0.0
    emitted_blocks: int = 0
    emitted_origins: int = 0
    memory_mib: float = 0.0
    last_error: str = ""


@dataclass(slots=True)
class StreamingState:
    plans_total: int = 0
    plans_started: int = 0
    plans_done: int = 0
    blocks_emitted: int = 0
    origins_emitted: int = 0
    events_fetched: int = 0
    started_at: float = field(default_factory=time.perf_counter)
    slots: list[WorkerSlot] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)


class WorkerMemoryManager:
    def __init__(self, limit_mib: int) -> None:
        self.limit_bytes = max(1, int(limit_mib)) * 1024 * 1024
        self.items: OrderedDict[str, int] = OrderedDict()
        self.bytes_used = 0

    def register(self, key: str, frame: pl.DataFrame) -> None:
        size = estimate_frame_bytes(frame)
        self.items[key] = size
        self.items.move_to_end(key)
        self.bytes_used += size
        self.evict_until_under_limit()

    def release(self, key: str) -> None:
        size = self.items.pop(key, 0)
        self.bytes_used = max(0, self.bytes_used - size)

    def evict_until_under_limit(self) -> None:
        while self.bytes_used > self.limit_bytes and self.items:
            _, size = self.items.popitem(last=False)
            self.bytes_used = max(0, self.bytes_used - size)

    @property
    def memory_mib(self) -> float:
        return self.bytes_used / (1024 * 1024)


class ActiveQueryRegistry:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._lock = threading.RLock()
        self._queries: dict[str, str] = {}

    def register(self, query_id: str, label: str) -> None:
        with self._lock:
            self._queries[query_id] = label

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._queries.pop(query_id, None)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._queries)


class ClickHouseTickerStreamDataset:
    def __init__(self, config: ClickHouseTickerStreamConfig) -> None:
        self.config = config
        self.state = StreamingState(slots=[WorkerSlot(worker_id=i) for i in range(max(1, int(config.ticker_workers)))])
        self.active_queries = ActiveQueryRegistry(prefix=f"packed_stream_{uuid.uuid4().hex[:10]}_")
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._ready_queue: queue.Queue[PackedMarketBlock | _DoneSentinel | _ErrorSentinel] = queue.Queue(maxsize=max(1, int(config.ready_queue_blocks)))
        self._plan_queue: queue.Queue[TickerMonthPlan | None] = queue.Queue()
        self._plans: list[TickerMonthPlan] = []

    @property
    def event_feature_names(self) -> tuple[str, ...]:
        return STREAM_EVENT_FEATURE_NAMES

    @property
    def label_names(self) -> tuple[str, ...]:
        return generated_label_names(self.config.label_horizons_us)

    def plan(self) -> list[TickerMonthPlan]:
        if not self._plans:
            plans = load_ticker_month_plans(self.config, active_queries=self.active_queries)
            if self.config.shuffle_plans:
                import random

                rng = random.Random(int(self.config.seed))
                rng.shuffle(plans)
            if int(self.config.max_plans) > 0:
                plans = plans[: int(self.config.max_plans)]
            self._plans = plans
            with self.state.lock:
                self.state.plans_total = len(plans)
        return self._plans

    def iter_blocks(self, *, repeat: bool = False) -> Iterator[PackedMarketBlock]:
        del repeat
        self._start_workers()
        done_workers = 0
        emitted_blocks = 0
        try:
            while done_workers < len(self._workers):
                item = self._ready_queue.get()
                if isinstance(item, _DoneSentinel):
                    done_workers += 1
                    continue
                if isinstance(item, _ErrorSentinel):
                    raise RuntimeError(item.error)
                emitted_blocks += 1
                if int(self.config.max_blocks) > 0 and emitted_blocks > int(self.config.max_blocks):
                    self.stop()
                    return
                yield item
        finally:
            self.stop()

    def _start_workers(self) -> None:
        if self._workers:
            return
        for plan in self.plan():
            self._plan_queue.put(plan)
        for _ in range(max(1, int(self.config.ticker_workers))):
            self._plan_queue.put(None)
        for worker_id in range(max(1, int(self.config.ticker_workers))):
            thread = threading.Thread(target=self._worker_loop, args=(worker_id,), name=f"packed-ticker-stream-{worker_id:02d}", daemon=True)
            thread.start()
            self._workers.append(thread)

    def _worker_loop(self, worker_id: int) -> None:
        slot = self.state.slots[worker_id]
        memory = WorkerMemoryManager(limit_mib=int(self.config.worker_memory_limit_mib))
        try:
            while not self._stop_event.is_set():
                plan = self._plan_queue.get()
                if plan is None:
                    break
                with self.state.lock:
                    self.state.plans_started += 1
                    slot.status = "planning"
                    slot.ticker = plan.ticker
                    slot.month = plan.month
                for job in build_block_jobs(self.config, plan):
                    if self._stop_event.is_set():
                        break
                    block = self._build_block(worker_id, slot, memory, job)
                    if block is None:
                        continue
                    if not self._put_ready(block):
                        break
                    with self.state.lock:
                        self.state.blocks_emitted += 1
                        self.state.origins_emitted += int(block.origin_count)
                        slot.emitted_blocks += 1
                        slot.emitted_origins += int(block.origin_count)
                with self.state.lock:
                    self.state.plans_done += 1
                    slot.status = "idle"
                    slot.current_job = ""
        except Exception as exc:  # noqa: BLE001
            slot.status = "error"
            slot.last_error = repr(exc)
            self._put_ready(_ErrorSentinel(error=repr(exc)))
        finally:
            self._put_ready(_DoneSentinel())

    def _put_ready(self, item: PackedMarketBlock | _DoneSentinel | _ErrorSentinel) -> bool:
        while not self._stop_event.is_set():
            try:
                self._ready_queue.put(item, timeout=0.25)
                return True
            except queue.Full:
                continue
        return False

    def _build_block(self, worker_id: int, slot: WorkerSlot, memory: WorkerMemoryManager, job: TickerBlockJob) -> PackedMarketBlock | None:
        with self.state.lock:
            slot.status = "fetch"
            slot.current_job = f"{job.plan.ticker} {job.plan.month} {job.origin_start_ordinal}-{job.origin_end_ordinal}"
        fetch_start = time.perf_counter()
        events = fetch_event_frame(self.config, job, active_queries=self.active_queries)
        fetch_seconds = time.perf_counter() - fetch_start
        if events.is_empty():
            return None
        memory_key = f"{job.plan.ticker}|{job.block_id}"
        memory.register(memory_key, events)
        with self.state.lock:
            self.state.events_fetched += int(events.height)
            slot.event_rows = int(events.height)
            slot.fetch_seconds = float(fetch_seconds)
            slot.memory_mib = memory.memory_mib
            slot.status = "process"
        process_start = time.perf_counter()
        block = build_packed_block_from_events(self.config, job, events, worker_id=worker_id)
        process_seconds = time.perf_counter() - process_start
        memory.release(memory_key)
        with self.state.lock:
            slot.process_seconds = float(process_seconds)
            slot.origin_rows = int(block.origin_count)
            slot.memory_mib = memory.memory_mib
            slot.status = "ready"
        return block

    def stop(self) -> None:
        self._stop_event.set()
        cancel_process_clickhouse_queries(self.config, self.active_queries)
        for thread in self._workers:
            if thread.is_alive():
                thread.join(timeout=1.0)

    def state_dict(self) -> dict[str, Any]:
        with self.state.lock:
            return {
                "plans_total": int(self.state.plans_total),
                "plans_started": int(self.state.plans_started),
                "plans_done": int(self.state.plans_done),
                "blocks_emitted": int(self.state.blocks_emitted),
                "origins_emitted": int(self.state.origins_emitted),
            }

    def load_state_dict(self, _value: Mapping[str, Any]) -> None:
        # Streaming ready order is intentionally concurrent. Exact replay should use one worker and deterministic plans.
        return None

    def telemetry_snapshot(self) -> dict[str, float | str]:
        with self.state.lock:
            elapsed = max(0.001, time.perf_counter() - self.state.started_at)
            snapshot: dict[str, float | str] = {
                "loader/state/phase": "clickhouse_ticker_stream",
                "loader/state/plans_total": float(self.state.plans_total),
                "loader/state/plans_started": float(self.state.plans_started),
                "loader/state/plans_done": float(self.state.plans_done),
                "loader/state/blocks_emitted": float(self.state.blocks_emitted),
                "loader/state/origins_emitted": float(self.state.origins_emitted),
                "loader/state/events_fetched": float(self.state.events_fetched),
                "loader/state/ready_queue": float(self._ready_queue.qsize()),
                "loader/state/ready_queue_limit": float(self._ready_queue.maxsize),
                "loader/state/origins_per_second": float(self.state.origins_emitted / elapsed),
            }
            for slot in self.state.slots[: min(8, len(self.state.slots))]:
                prefix = f"loader/worker_{slot.worker_id:02d}/"
                snapshot[prefix + "status"] = slot.status
                snapshot[prefix + "ticker"] = slot.ticker
                snapshot[prefix + "event_rows"] = float(slot.event_rows)
                snapshot[prefix + "origin_rows"] = float(slot.origin_rows)
                snapshot[prefix + "fetch_seconds"] = float(slot.fetch_seconds)
                snapshot[prefix + "process_seconds"] = float(slot.process_seconds)
                snapshot[prefix + "memory_mib"] = float(slot.memory_mib)
            return snapshot


@dataclass(frozen=True, slots=True)
class _DoneSentinel:
    pass


@dataclass(frozen=True, slots=True)
class _ErrorSentinel:
    error: str


def load_ticker_month_plans(config: ClickHouseTickerStreamConfig, *, active_queries: ActiveQueryRegistry) -> list[TickerMonthPlan]:
    if not config.months:
        raise ValueError("At least one YYYY-MM month is required for the ClickHouse ticker stream loader.")
    selected_months = tuple(sorted(set(config.months)))
    start = min(month_start(month) for month in selected_months)
    end = add_months(max(month_start(month) for month in selected_months), 1)
    ticker_filter = ""
    if config.tickers:
        quoted = ", ".join(sql_string(ticker.upper()) for ticker in config.tickers)
        ticker_filter = f"AND upper(ticker) IN ({quoted})"
    month_filter = ", ".join(sql_string(month) for month in selected_months)
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_ticker_day_index_table)}"
    query = f"""
SELECT
    upper(ticker) AS ticker,
    formatDateTime(toStartOfMonth(source_date), '%Y-%m') AS month,
    toUInt64(sum(event_count)) AS event_count,
    toUInt64(min(first_ordinal)) AS first_origin_ordinal,
    toUInt64(max(last_ordinal)) AS last_origin_ordinal,
    toUInt64(min(first_sip_timestamp_us)) AS first_timestamp_us,
    toUInt64(max(last_sip_timestamp_us)) AS last_timestamp_us
FROM {table}
WHERE source_date >= toDate({sql_string(start.isoformat())})
  AND source_date < toDate({sql_string(end.isoformat())})
  AND formatDateTime(toStartOfMonth(source_date), '%Y-%m') IN ({month_filter})
  {ticker_filter}
GROUP BY ticker, month
HAVING event_count > 0
ORDER BY event_count DESC, ticker ASC
{settings_sql(config)}
"""
    frame = query_polars(config, query, active_queries=active_queries, label="plan")
    plans: list[TickerMonthPlan] = []
    for index, row in enumerate(frame.iter_rows(named=True)):
        plans.append(
            TickerMonthPlan(
                plan_index=index,
                ticker=str(row["ticker"]).upper(),
                month=str(row["month"]),
                event_count=int(row["event_count"]),
                first_origin_ordinal=int(row["first_origin_ordinal"]),
                last_origin_ordinal=int(row["last_origin_ordinal"]),
                first_timestamp_us=int(row["first_timestamp_us"]),
                last_timestamp_us=int(row["last_timestamp_us"]),
            )
        )
    return plans


def build_block_jobs(config: ClickHouseTickerStreamConfig, plan: TickerMonthPlan) -> Iterator[TickerBlockJob]:
    origin_start = int(plan.first_origin_ordinal)
    block_id = 0
    while origin_start <= int(plan.last_origin_ordinal):
        origin_end = min(int(plan.last_origin_ordinal), origin_start + max(1, int(config.target_origin_count_per_block)) - 1)
        fetch_start = max(1, origin_start - max(1, int(config.event_context_rows)) + 1)
        fetch_end = origin_end + max(0, int(config.future_event_guard_rows))
        yield TickerBlockJob(
            plan=plan,
            block_id=block_id,
            origin_start_ordinal=origin_start,
            origin_end_ordinal=origin_end,
            fetch_start_ordinal=fetch_start,
            fetch_end_ordinal=fetch_end,
        )
        block_id += 1
        origin_start = origin_end + 1


def fetch_event_frame(config: ClickHouseTickerStreamConfig, job: TickerBlockJob, *, active_queries: ActiveQueryRegistry) -> pl.DataFrame:
    table = events_source_table(config, job)
    query = event_query(config, job, table)
    return query_polars(config, query, active_queries=active_queries, label=f"events_{job.plan.ticker}_{job.block_id:06d}")


def event_query(config: ClickHouseTickerStreamConfig, job: TickerBlockJob, table: str) -> str:
    return f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    dateDiff('second', toStartOfDay(ts_utc), ts_utc) AS utc_second,
    toDayOfWeek(ts_utc) AS utc_dow,
    toDayOfYear(ts_utc) AS utc_doy,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
    cityHash64(ticker) AS ticker_id,
    ticker AS event_ticker,
    toUInt64(ordinal) AS ordinal,
    event_meta,
    toUInt64(sip_timestamp_us) AS timestamp_us,
    price_primary_int,
    price_secondary_int,
    toFloat32(size_primary) AS size_primary,
    toFloat32(size_secondary) AS size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    toFloat32(sin(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_cos,
    toFloat32(toYear(ts_utc) - 2000 + (utc_doy - 1) / 366.0) AS years_since_2000,
    toDate(ts_local) AS local_date,
    toUInt64(dateDiff('microsecond', toStartOfDay(ts_local), ts_local)) AS local_session_us,
    toUInt32(local_second) AS session_second,
    toFloat32(greatest(0, least({SESSION_LENGTH_SECOND}, local_second - {SESSION_START_SECOND})) / {float(SESSION_LENGTH_SECOND)}) AS session_progress,
    toUInt8(local_second >= {SESSION_REGULAR_START_SECOND} AND local_second < {SESSION_REGULAR_END_SECOND}) AS is_regular_hours,
    toUInt8(local_second >= {SESSION_START_SECOND} AND local_second < {SESSION_REGULAR_START_SECOND}) AS is_premarket,
    toUInt8(local_second >= {SESSION_REGULAR_END_SECOND} AND local_second < {SESSION_END_SECOND}) AS is_afterhours
FROM {table}
PREWHERE ticker = {sql_string(job.plan.ticker)}
  AND ordinal >= {int(job.fetch_start_ordinal)}
  AND ordinal <= {int(job.fetch_end_ordinal)}
ORDER BY ticker, ordinal
{settings_sql(config)}
"""


def events_source_table(config: ClickHouseTickerStreamConfig, job: TickerBlockJob) -> str:
    base = str(config.events_table_base)
    year = int(job.plan.month[:4])
    month = int(job.plan.month[5:7])
    years = {year}
    if month == 1 and job.fetch_start_ordinal < job.plan.first_origin_ordinal:
        years.add(year - 1)
    if month == 12 and job.fetch_end_ordinal > job.plan.last_origin_ordinal:
        years.add(year + 1)
    existing = [f"{base}_{item}" for item in sorted(years) if item >= 1990]
    if len(existing) == 1:
        return f"{quote_ident(config.database)}.{quote_ident(existing[0])}"
    pattern = "^(" + "|".join(name.replace(".", "\\.") for name in existing) + ")$"
    return f"merge({sql_string(config.database)}, {sql_string(pattern)})"


def build_packed_block_from_events(config: ClickHouseTickerStreamConfig, job: TickerBlockJob, events: pl.DataFrame, *, worker_id: int) -> PackedMarketBlock:
    events = events.sort("ordinal")
    ordinals = events["ordinal"].to_numpy().astype(np.int64, copy=False)
    timestamps = events["timestamp_us"].to_numpy().astype(np.int64, copy=False)
    origin_mask = (ordinals >= int(job.origin_start_ordinal)) & (ordinals <= int(job.origin_end_ordinal))
    origin_indices = np.flatnonzero(origin_mask).astype(np.int64, copy=False)
    if origin_indices.size == 0:
        raise RuntimeError(f"No origin rows found for {job.plan.ticker} {job.origin_start_ordinal}-{job.origin_end_ordinal}")
    visible_end = int(origin_indices[-1]) + 1
    visible = events.slice(0, visible_end)
    features = visible.select(list(STREAM_EVENT_FEATURE_NAMES)).to_numpy().astype(np.float32, copy=False)
    origin_ordinals = ordinals[origin_indices]
    origin_timestamps = timestamps[origin_indices]
    labels, masks = build_vectorized_intraday_labels(config, events, origin_indices)
    manifest = PackedBlockManifest(
        block_id=f"{job.plan.month}|{job.plan.ticker}|{job.block_id:06d}",
        month=job.plan.month,
        ticker=job.plan.ticker,
        ticker_dir_name=job.plan.ticker,
        source_cache_root="clickhouse",
        event_path="",
        origin_path="",
        label_path=None,
        event_feature_names=STREAM_EVENT_FEATURE_NAMES,
        event_rows=int(features.shape[0]),
        origin_rows=int(origin_indices.shape[0]),
        event_start_index=0,
        event_end_index=int(features.shape[0]),
        origin_start_index=0,
        origin_end_index=int(origin_indices.shape[0]),
        first_origin_timestamp_us=int(origin_timestamps[0]),
        last_origin_timestamp_us=int(origin_timestamps[-1]),
        first_origin_ordinal=int(origin_ordinals[0]),
        last_origin_ordinal=int(origin_ordinals[-1]),
        first_event_ordinal=int(ordinals[0]),
        last_event_ordinal=int(ordinals[visible_end - 1]),
        created_at_utc=utc_now_iso(),
        metadata={
            "source": "clickhouse_ticker_stream",
            "worker_id": int(worker_id),
            "fetch_start_ordinal": int(job.fetch_start_ordinal),
            "fetch_end_ordinal": int(job.fetch_end_ordinal),
            "future_support_event_rows": int(max(0, events.height - visible_end)),
        },
    )
    return PackedMarketBlock(
        block_manifest=manifest,
        events=features,
        origin_positions=origin_indices.astype(np.int64, copy=False),
        origin_ordinals=origin_ordinals.astype(np.int64, copy=False),
        origin_timestamp_us=origin_timestamps.astype(np.int64, copy=False),
        event_ordinals=ordinals[:visible_end].astype(np.int64, copy=False),
        event_timestamp_us=timestamps[:visible_end].astype(np.int64, copy=False),
        labels=labels,
        label_masks=masks,
        metadata=manifest.metadata,
    )


def build_vectorized_intraday_labels(config: ClickHouseTickerStreamConfig, events: pl.DataFrame, origin_indices: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    timestamps = events["timestamp_us"].to_numpy().astype(np.int64, copy=False)
    origin_timestamps = timestamps[origin_indices]
    primary = events["price_primary_int"].to_numpy().astype(np.float32, copy=False)
    secondary = events["price_secondary_int"].to_numpy().astype(np.float32, copy=False)
    size_primary = events["size_primary"].to_numpy().astype(np.float32, copy=False)
    size_secondary = events["size_secondary"].to_numpy().astype(np.float32, copy=False)
    prefix_primary = np.concatenate(([0.0], np.cumsum(size_primary, dtype=np.float64))).astype(np.float64, copy=False)
    prefix_secondary = np.concatenate(([0.0], np.cumsum(size_secondary, dtype=np.float64))).astype(np.float64, copy=False)
    labels: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    any_available = np.zeros(origin_indices.shape[0], dtype=np.bool_)
    n = timestamps.shape[0]
    for name, horizon_us in config.label_horizons_us.items():
        starts = origin_indices + 1
        ends = np.searchsorted(timestamps, origin_timestamps + int(horizon_us), side="right").astype(np.int64, copy=False)
        ends = np.minimum(ends, n)
        available = ends > starts
        any_available |= available
        safe_last = np.maximum(starts, ends - 1)
        count = np.maximum(0, ends - starts).astype(np.float32, copy=False)
        labels[f"future_event_count_{name}"] = count
        masks[f"future_event_count_{name}"] = np.ones_like(available, dtype=np.bool_)
        labels[f"future_price_primary_int_{name}"] = np.where(available, primary[safe_last], 0.0).astype(np.float32, copy=False)
        labels[f"future_price_secondary_int_{name}"] = np.where(available, secondary[safe_last], 0.0).astype(np.float32, copy=False)
        masks[f"future_price_primary_int_{name}"] = available
        masks[f"future_price_secondary_int_{name}"] = available
        labels[f"future_size_primary_sum_{name}"] = (prefix_primary[ends] - prefix_primary[starts]).astype(np.float32, copy=False)
        labels[f"future_size_secondary_sum_{name}"] = (prefix_secondary[ends] - prefix_secondary[starts]).astype(np.float32, copy=False)
        masks[f"future_size_primary_sum_{name}"] = np.ones_like(available, dtype=np.bool_)
        masks[f"future_size_secondary_sum_{name}"] = np.ones_like(available, dtype=np.bool_)
    masks["available"] = any_available
    return labels, masks


def generated_label_names(horizons_us: Mapping[str, int]) -> tuple[str, ...]:
    names: list[str] = []
    for horizon in horizons_us:
        names.extend(
            [
                f"future_event_count_{horizon}",
                f"future_price_primary_int_{horizon}",
                f"future_price_secondary_int_{horizon}",
                f"future_size_primary_sum_{horizon}",
                f"future_size_secondary_sum_{horizon}",
            ]
        )
    return tuple(names)


def query_polars(config: ClickHouseTickerStreamConfig, query: str, *, active_queries: ActiveQueryRegistry, label: str) -> pl.DataFrame:
    query_id = f"{active_queries.prefix}{label}_{uuid.uuid4().hex}"
    active_queries.register(query_id, label)
    attempt = 0
    try:
        while True:
            try:
                return _query_polars_once(config, query, query_id=query_id)
            except Exception as exc:
                if attempt >= int(config.query_retries) or not is_transient_clickhouse_error(exc):
                    raise
                time.sleep(max(0.0, float(config.query_retry_backoff_seconds)) * float(2**attempt))
                attempt += 1
    finally:
        active_queries.unregister(query_id)


def _query_polars_once(config: ClickHouseTickerStreamConfig, query: str, *, query_id: str) -> pl.DataFrame:
    try:
        import clickhouse_connect  # type: ignore
    except ModuleNotFoundError:
        return _query_polars_http_arrow(config, query, query_id=query_id)
    parsed = urllib_parse.urlparse(str(config.clickhouse_url))
    secure = parsed.scheme == "https"
    client = clickhouse_connect.get_client(
        host=parsed.hostname or "localhost",
        port=parsed.port or (8443 if secure else 8123),
        username=str(config.user or "default"),
        password=str(config.password or ""),
        secure=secure,
    )
    try:
        try:
            table = client.query_arrow(query, query_id=query_id)
        except TypeError:
            return _query_polars_http_arrow(config, query, query_id=query_id)
        return pl.from_arrow(table)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _query_polars_http_arrow(config: ClickHouseTickerStreamConfig, query: str, *, query_id: str) -> pl.DataFrame:
    sql = query.strip().rstrip(";").rstrip()
    if " FORMAT " not in f" {sql[-64:].upper()} ":
        sql += "\nFORMAT ArrowStream"
    url = str(config.clickhouse_url).rstrip("/") + "/?" + urllib_parse.urlencode({"query_id": query_id})
    req = urllib_request.Request(url, data=sql.encode("utf-8"), method="POST")
    if config.user:
        req.add_header("X-ClickHouse-User", config.user)
    if config.password:
        req.add_header("X-ClickHouse-Key", config.password)
    try:
        with urllib_request.urlopen(req, timeout=None) as response:
            data = response.read()
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc
    try:
        import pyarrow as pa  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install pyarrow to use ClickHouse HTTP ArrowStream fallback.") from exc
    with pa.ipc.open_stream(BytesIO(data)) as reader:
        return pl.from_arrow(reader.read_all())


def cancel_process_clickhouse_queries(config: ClickHouseTickerStreamConfig, active_queries: ActiveQueryRegistry) -> int:
    snapshot = active_queries.snapshot()
    if not snapshot:
        return 0
    prefix = active_queries.prefix + "%"
    client = ClickHouseHttpClient(config.clickhouse_url, config.user, config.password)
    try:
        client.execute(f"KILL QUERY WHERE query_id LIKE {sql_string(prefix)} ASYNC")
    except Exception:
        return 0
    return len(snapshot)


def is_transient_clickhouse_error(exc: BaseException) -> bool:
    text = repr(exc)
    if "QUERY_WAS_CANCELLED" in text or "DB::Exception" in text:
        return False
    return any(marker in text for marker in ("IncompleteRead", "ProtocolError", "RemoteDisconnected", "Connection reset", "timed out", "Connection broken"))


def settings_sql(config: ClickHouseTickerStreamConfig) -> str:
    settings: list[str] = []
    if int(config.max_threads_per_query) > 0:
        settings.append(f"max_threads = {int(config.max_threads_per_query)}")
    if str(config.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}")
    if not settings:
        return ""
    return "SETTINGS " + ", ".join(settings)


def month_start(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def add_months(value: date, count: int) -> date:
    month_index = value.year * 12 + value.month - 1 + int(count)
    return date(month_index // 12, month_index % 12 + 1, 1)


def estimate_frame_bytes(frame: pl.DataFrame) -> int:
    try:
        return int(frame.estimated_size())
    except Exception:
        return int(frame.height * max(1, len(frame.columns)) * 8)
