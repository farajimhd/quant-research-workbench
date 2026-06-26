from __future__ import annotations

import datetime as dt
import json
import queue
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlparse

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string
from research.mlops.clickhouse_events import EVENT_ROW_DTYPE
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.data.contracts import RollingSampleIndex, RollingTrainingBatch
from research.mlops.data.rolling import (
    MacroBarFrame,
    RollingMarketSampleEngine,
    max_daily_label_lookahead_days,
    max_daily_macro_lookback_days,
)


EVENT_COLUMNS: tuple[str, ...] = (
    "ordinal",
    "event_type",
    "sip_timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "event_flags",
    "conditions_packed",
)

NEWS_TOKEN_COLUMNS: tuple[str, ...] = (
    "ticker",
    "timestamp_us",
    "source_id",
    "provider",
    "provider_article_id",
    "title",
    "article_url",
    "url_domain",
    "channels",
    "provider_tags",
    "quality_flags",
    "tokenizer_model",
    "max_tokens",
    "token_chunk_index",
    "token_start",
    "token_end",
    "original_token_count",
    "token_count",
    "padding_tokens",
    "was_truncated",
    "input_ids",
    "attention_mask",
    "text_hash",
    "text_char_count",
    "source_text_char_count",
    "text_prefix_truncated",
)

SEC_TOKEN_COLUMNS: tuple[str, ...] = (
    "ticker",
    "timestamp_us",
    "source_id",
    "accession_number",
    "cik",
    "form_type",
    "text_rank",
    "document_id",
    "text_kind",
    "quality_flags",
    "tokenizer_model",
    "max_tokens",
    "token_chunk_index",
    "token_start",
    "token_end",
    "original_token_count",
    "token_count",
    "padding_tokens",
    "was_truncated",
    "input_ids",
    "attention_mask",
    "text_hash",
    "text_char_count",
    "source_text_char_count",
    "text_prefix_truncated",
)


@dataclass(frozen=True, slots=True)
class StreamingEventBlock:
    start_date: dt.date
    end_date: dt.date
    frame: Any
    rows_by_ticker: dict[str, np.ndarray]
    row_count: int
    ticker_count: int
    min_timestamp_us: int
    max_timestamp_us: int


@dataclass(frozen=True, slots=True)
class StreamingContextBlock:
    start_timestamp_us: int
    end_timestamp_us: int
    rows_by_context: dict[str, list[dict[str, Any]]]

    @property
    def row_count(self) -> int:
        return int(sum(len(rows) for rows in self.rows_by_context.values()))

    def counts(self) -> dict[str, int]:
        return {name: int(len(rows)) for name, rows in sorted(self.rows_by_context.items())}


@dataclass(frozen=True, slots=True)
class StreamingBatchEnvelope:
    batch: RollingTrainingBatch
    samples: tuple[RollingSampleIndex, ...]
    batch_index: int
    block_index: int
    source_queue_wait_seconds: float = 0.0


@dataclass(slots=True)
class StreamingStageRecord:
    stage: str
    seconds: float
    rss_before_mib: float
    rss_after_mib: float
    metadata: dict[str, Any] = field(default_factory=dict)


class StreamingProfiler:
    def __init__(self, *, output_path: Path | None = None) -> None:
        self.output_path = output_path
        self.records: list[StreamingStageRecord] = []
        self._lock = threading.Lock()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)

    def stage(self, name: str, **metadata: Any) -> "_StreamingStageTimer":
        return _StreamingStageTimer(self, name, metadata)

    def add(self, record: StreamingStageRecord) -> None:
        with self._lock:
            self.records.append(record)
            if self.output_path is not None:
                with self.output_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_jsonable_record(record), sort_keys=True) + "\n")

    def aggregate(self) -> dict[str, Any]:
        stages: dict[str, dict[str, Any]] = {}
        for record in self.records:
            item = stages.setdefault(
                record.stage,
                {"seconds": 0.0, "count": 0, "max_rss_after_mib": 0.0, "max_rss_delta_mib": 0.0},
            )
            item["seconds"] += float(record.seconds)
            item["count"] += 1
            item["max_rss_after_mib"] = max(float(item["max_rss_after_mib"]), float(record.rss_after_mib))
            item["max_rss_delta_mib"] = max(
                float(item["max_rss_delta_mib"]),
                float(record.rss_after_mib) - float(record.rss_before_mib),
            )
        return {"stages": dict(sorted(stages.items())), "records": len(self.records)}


class _StreamingStageTimer:
    def __init__(self, profiler: StreamingProfiler, stage: str, metadata: dict[str, Any]) -> None:
        self.profiler = profiler
        self.stage = stage
        self.metadata = metadata
        self.started = 0.0
        self.rss_before = 0.0

    def __enter__(self) -> "_StreamingStageTimer":
        self.rss_before = current_rss_mib()
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        seconds = time.perf_counter() - self.started
        metadata = dict(self.metadata)
        if exc is not None:
            metadata["error"] = repr(exc)
        self.profiler.add(
            StreamingStageRecord(
                stage=self.stage,
                seconds=float(seconds),
                rss_before_mib=float(self.rss_before),
                rss_after_mib=float(current_rss_mib()),
                metadata=metadata,
            )
        )


class StreamingClickHouseTrainingSource:
    """Bulk ClickHouse source for the streaming training implementation.

    Event reads intentionally avoid ticker arrays and use the current event
    table's date partitioning. Arrow/Polars is used at the source boundary; the
    shared rolling engine still receives compact NumPy event rows.
    """

    def __init__(self, *, config: RollingMarketDataConfig, clickhouse_url: str, user: str, password: str) -> None:
        self.config = config
        self.clickhouse_url = clickhouse_url
        self.user = user
        self.password = password
        self.text_client = ClickHouseHttpClient(clickhouse_url, user, password)
        self._client: Any | None = None
        self._pl: Any | None = None
        self.profiler: StreamingProfiler | None = None

    def close(self) -> None:
        client = self._client
        if client is not None and hasattr(client, "close"):
            client.close()
        self._client = None

    def fetch_event_block(
        self,
        *,
        start_date: dt.date,
        end_date: dt.date,
        row_limit: int = 0,
    ) -> StreamingEventBlock:
        frame = self.fetch_event_frame(start_date=start_date, end_date=end_date, row_limit=row_limit)
        return self.event_frame_to_block(frame=frame, start_date=start_date, end_date=end_date)

    def fetch_event_frame(
        self,
        *,
        start_date: dt.date,
        end_date: dt.date,
        row_limit: int = 0,
    ) -> Any:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.events_table)}"
        limit_sql = f"\nLIMIT {int(row_limit)}" if int(row_limit) > 0 else ""
        query = f"""
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
    event_flags,
    conditions_packed
FROM {table}
PREWHERE event_date >= toDate({sql_string(start_date.isoformat())})
  AND event_date < toDate({sql_string(end_date.isoformat())})
ORDER BY ticker, ordinal
{limit_sql}
{self._settings()}
"""
        return self.query_polars(query)

    def event_frame_to_block(self, *, frame: Any, start_date: dt.date, end_date: dt.date) -> StreamingEventBlock:
        rows_by_ticker = self.events_frame_to_rows_by_ticker(frame)
        if frame.height:
            min_ts = int(frame.select(self._pl.col("sip_timestamp_us").min()).item())
            max_ts = int(frame.select(self._pl.col("sip_timestamp_us").max()).item())
        else:
            min_ts = 0
            max_ts = 0
        return StreamingEventBlock(
            start_date=start_date,
            end_date=end_date,
            frame=frame,
            rows_by_ticker=rows_by_ticker,
            row_count=int(frame.height),
            ticker_count=len(rows_by_ticker),
            min_timestamp_us=min_ts,
            max_timestamp_us=max_ts,
        )

    def events_frame_to_rows_by_ticker(self, frame: Any) -> dict[str, np.ndarray]:
        pl = self._polars()
        if frame.height == 0:
            return {}
        normalized = frame.with_columns(pl.col("ticker").cast(pl.Utf8).str.to_uppercase())
        out: dict[str, np.ndarray] = {}
        parts = normalized.partition_by("ticker", as_dict=True, maintain_order=True)
        for span_id, (key, part) in enumerate(parts.items()):
            ticker = key[0] if isinstance(key, tuple) else key
            rows = np.zeros((int(part.height),), dtype=EVENT_ROW_DTYPE)
            rows["span_id"] = int(span_id)
            for name in EVENT_COLUMNS:
                rows[name] = np.asarray(part.get_column(name).to_numpy())
            out[str(ticker).upper()] = rows
        return out

    def fetch_category_references(self) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.category_reference_table)}"
        query = f"""
SELECT
    domain,
    field_name,
    category_value,
    argMax(category_id, updated_at) AS category_id,
    argMax(one_hot_index, updated_at) AS one_hot_index
FROM {table}
GROUP BY
    domain,
    field_name,
    category_value
ORDER BY
    domain,
    field_name,
    category_id
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def fetch_macro_bars_1d(
        self,
        *,
        start_date: dt.date,
        end_date: dt.date,
    ) -> MacroBarFrame:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.macro_bars_table)}"
        macro_lookback_days = max(
            int(self.config.macro_lookback_days),
            max_daily_macro_lookback_days() * 2,
        )
        label_lookahead_days = max(int(self.config.label_lookahead_days), max_daily_label_lookahead_days())
        macro_start = start_date - dt.timedelta(days=macro_lookback_days)
        label_end = end_date + dt.timedelta(days=label_lookahead_days)
        query = f"""
SELECT
    sym,
    timeframe,
    toUnixTimestamp64Milli(bar_start) AS bar_start_ms,
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
WHERE timeframe = '1d'
  AND bar_start >= toDateTime64({sql_string(macro_start.isoformat() + " 00:00:00")}, 3, 'UTC')
  AND bar_start < toDateTime64({sql_string(label_end.isoformat() + " 00:00:00")}, 3, 'UTC')
ORDER BY sym, timeframe, bar_start
FORMAT JSONEachRow
"""
        started = time.perf_counter()
        rows = [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]
        return MacroBarFrame(rows=rows, fetch_seconds=time.perf_counter() - started)

    def fetch_token_contexts(
        self,
        *,
        start_timestamp_us: int,
        end_timestamp_us: int,
        include_lookback: bool,
        include_xbrl: bool = True,
    ) -> StreamingContextBlock:
        start_us = int(start_timestamp_us)
        if include_lookback:
            news_start_us = max(0, start_us - int(self.config.news_lookback_days) * 86_400_000_000)
            sec_start_us = max(0, start_us - int(self.config.sec_lookback_days) * 86_400_000_000)
            xbrl_start_us = max(0, start_us - int(self.config.xbrl_lookback_days) * 86_400_000_000)
        else:
            news_start_us = start_us
            sec_start_us = start_us
            xbrl_start_us = start_us
        end_us = int(end_timestamp_us)
        rows: dict[str, list[dict[str, Any]]] = {}
        enabled = set(self.config.q_live_contexts)
        if "ticker_news" in enabled:
            with self._stage("ticker_news_tokens_fetch", start_us=news_start_us, end_us=end_us):
                rows["ticker_news"] = self._fetch_ticker_news_tokens(start_us=news_start_us, end_us=end_us)
        if "market_news" in enabled:
            with self._stage("market_news_tokens_fetch", start_us=news_start_us, end_us=end_us):
                rows["market_news"] = self._fetch_market_news_tokens(start_us=news_start_us, end_us=end_us)
        if "sec_filings" in enabled:
            with self._stage("sec_filing_tokens_fetch", start_us=sec_start_us, end_us=end_us):
                rows["sec_filings"] = self._fetch_sec_tokens(start_us=sec_start_us, end_us=end_us)
        if include_xbrl and "xbrl" in enabled:
            with self._stage("xbrl_context_fetch", start_us=xbrl_start_us, end_us=end_us):
                rows["xbrl"] = self._fetch_xbrl(start_us=xbrl_start_us, end_us=end_us)
        return StreamingContextBlock(start_timestamp_us=start_us, end_timestamp_us=end_us, rows_by_context=rows)

    def _fetch_ticker_news_tokens(self, *, start_us: int, end_us: int) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.news_token_table)}"
        columns = ",\n    ".join(quote_ident(column) for column in NEWS_TOKEN_COLUMNS)
        query = f"""
SELECT
    {columns}
FROM {table}
WHERE timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(end_us)}
ORDER BY ticker, timestamp_us, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_market_news_tokens(self, *, start_us: int, end_us: int) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.database)}.{quote_ident(self.config.news_token_table)}"
        source_columns = ",\n        ".join(f"t.{quote_ident(column)}" for column in NEWS_TOKEN_COLUMNS if column != "ticker")
        query = f"""
SELECT
    '__MARKET__' AS ticker,
    {source_columns}
FROM
(
    SELECT *
    FROM {table}
    WHERE timestamp_us >= {int(start_us)}
      AND timestamp_us < {int(end_us)}
    ORDER BY source_id, token_chunk_index, ticker
    LIMIT 1 BY source_id, token_chunk_index
) AS t
ORDER BY timestamp_us, source_id, token_chunk_index
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_sec_tokens(self, *, start_us: int, end_us: int) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.sec_filing_text_token_table)}"
        columns = ",\n    ".join(quote_ident(column) for column in SEC_TOKEN_COLUMNS)
        query = f"""
SELECT
    {columns}
FROM {table}
WHERE timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(end_us)}
ORDER BY ticker, timestamp_us, accession_number, text_rank, document_id, token_chunk_index
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def _fetch_xbrl(self, *, start_us: int, end_us: int) -> list[dict[str, Any]]:
        table = f"{quote_ident(self.config.sec_context_database)}.{quote_ident(self.config.sec_xbrl_context_table)}"
        query = f"""
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
WHERE timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(end_us)}
ORDER BY ticker, timestamp_us, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
FORMAT JSONEachRow
"""
        return [json.loads(line) for line in self.text_client.execute(query).splitlines() if line.strip()]

    def query_polars(self, query: str) -> Any:
        table = self._arrow_client().query_arrow(query)
        return self._polars().from_arrow(table)

    def _arrow_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import clickhouse_connect  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install clickhouse-connect in this environment to use the streaming Arrow source.") from exc
        parsed = urlparse(self.clickhouse_url)
        secure = parsed.scheme == "https"
        self._client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if secure else 8123),
            username=self.user,
            password=self.password,
            secure=secure,
        )
        return self._client

    def _polars(self) -> Any:
        if self._pl is not None:
            return self._pl
        try:
            import polars as pl  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install polars in this environment to use the streaming Arrow source.") from exc
        self._pl = pl
        return self._pl

    def _settings(self) -> str:
        settings: list[str] = []
        if int(self.config.max_threads) > 0:
            settings.append(f"max_threads = {int(self.config.max_threads)}")
        if str(self.config.max_memory_usage) != "0":
            settings.append(f"max_memory_usage = {parse_size_bytes(str(self.config.max_memory_usage))}")
        return "SETTINGS " + ", ".join(settings) if settings else ""

    def _stage(self, name: str, **metadata: Any) -> Any:
        if self.profiler is None:
            return nullcontext()
        return self.profiler.stage(name, **metadata)


class StreamingRollingTrainingProvider:
    """CPU-side streaming producer for historical rolling training batches."""

    def __init__(
        self,
        *,
        source: StreamingClickHouseTrainingSource,
        config: RollingMarketDataConfig,
        start_timestamp_us: int,
        end_timestamp_us: int,
        block_days: int = 3,
        warmup_days: int = 3,
        max_batches: int = 0,
        event_row_limit: int = 0,
        load_token_contexts: bool = True,
        load_xbrl: bool = True,
        ready_queue_size: int = 4,
        shutdown_timeout_seconds: float = 2.0,
        profiler: StreamingProfiler | None = None,
    ) -> None:
        self.source = source
        self.config = config
        self.start_timestamp_us = int(start_timestamp_us)
        self.end_timestamp_us = int(end_timestamp_us)
        self.block_days = max(1, int(block_days))
        self.warmup_days = max(0, int(warmup_days))
        self.max_batches = int(max_batches)
        self.event_row_limit = int(event_row_limit)
        self.load_token_contexts = bool(load_token_contexts)
        self.load_xbrl = bool(load_xbrl)
        self.ready_queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(ready_queue_size)))
        self.shutdown_timeout_seconds = max(0.0, float(shutdown_timeout_seconds))
        self.profiler = profiler or StreamingProfiler()
        self.source.profiler = self.profiler
        self.engine = RollingMarketSampleEngine(config)
        self._thread: threading.Thread | None = None
        self._sentinel = object()
        self._stop_event = threading.Event()
        self._finished_event = threading.Event()

    def __iter__(self) -> Iterator[StreamingBatchEnvelope]:
        self.start()
        try:
            while True:
                queued_at = time.perf_counter()
                try:
                    item = self.ready_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                wait_seconds = time.perf_counter() - queued_at
                if item is self._sentinel:
                    break
                if isinstance(item, BaseException):
                    raise item
                envelope = item
                if isinstance(envelope, StreamingBatchEnvelope):
                    yield StreamingBatchEnvelope(
                        batch=envelope.batch,
                        samples=envelope.samples,
                        batch_index=envelope.batch_index,
                        block_index=envelope.block_index,
                        source_queue_wait_seconds=wait_seconds,
                    )
        finally:
            self.stop(join_timeout=self.shutdown_timeout_seconds)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._finished_event.clear()
        self._thread = threading.Thread(target=self._run_worker, name="streaming-rolling-loader", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                self._thread = None

    def stop(self, *, join_timeout: float | None = None) -> None:
        self._stop_event.set()
        self.source.close()
        self._try_put_sentinel()
        self.join(timeout=join_timeout)

    def _run_worker(self) -> None:
        try:
            self._raise_if_stopped()
            self._initialize()
            self._raise_if_stopped()
            produced = self._stream_blocks()
            if not self._stop_event.is_set():
                self.profiler.add(
                    StreamingStageRecord(
                        stage="producer_complete",
                        seconds=0.0,
                        rss_before_mib=current_rss_mib(),
                        rss_after_mib=current_rss_mib(),
                        metadata={"batches": int(produced)},
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - worker must surface all failures to the consumer.
            if self._stop_event.is_set():
                self.profiler.add(
                    StreamingStageRecord(
                        stage="producer_cancelled",
                        seconds=0.0,
                        rss_before_mib=current_rss_mib(),
                        rss_after_mib=current_rss_mib(),
                        metadata={"reason": repr(exc)},
                    )
                )
            else:
                self._put_queue_item(exc)
        finally:
            self._finished_event.set()
            self._try_put_sentinel()

    def _initialize(self) -> None:
        start_date = date_from_us(self.start_timestamp_us)
        end_date = date_from_us(self.end_timestamp_us) + dt.timedelta(days=1)
        with self.profiler.stage("category_references_fetch"):
            references = self.source.fetch_category_references()
        self._raise_if_stopped()
        with self.profiler.stage("category_references_load", rows=len(references)):
            self.engine.load_category_references(references)
        self._raise_if_stopped()
        with self.profiler.stage("macro_bars_1d_full_fetch", start_date=start_date.isoformat(), end_date=end_date.isoformat()):
            macro = self.source.fetch_macro_bars_1d(start_date=start_date, end_date=end_date)
        self._raise_if_stopped()
        with self.profiler.stage("macro_bars_1d_full_load", rows=len(macro.rows), fetch_seconds=macro.fetch_seconds):
            self.engine.load_macro_bars(macro)
        self._raise_if_stopped()
        if self.load_token_contexts:
            with self.profiler.stage("initial_token_context_fetch"):
                context = self.source.fetch_token_contexts(
                    start_timestamp_us=self.start_timestamp_us,
                    end_timestamp_us=self.start_timestamp_us + 1,
                    include_lookback=True,
                    include_xbrl=self.load_xbrl,
                )
            self._raise_if_stopped()
            with self.profiler.stage("initial_token_context_load", rows=context.row_count, counts=context.counts()):
                self.engine.load_external_contexts(context.rows_by_context)

    def _stream_blocks(self) -> int:
        produced = 0
        cursor_date = date_from_us(self.start_timestamp_us) - dt.timedelta(days=self.warmup_days)
        end_date = date_from_us(self.end_timestamp_us) + dt.timedelta(days=1)
        block_index = 0
        while not self._stop_event.is_set() and cursor_date < end_date and (self.max_batches <= 0 or produced < self.max_batches):
            block_start = cursor_date
            block_end = min(end_date, cursor_date + dt.timedelta(days=self.block_days))
            with self.profiler.stage("event_block_query_arrow_polars", block_index=block_index, start_date=block_start.isoformat(), end_date=block_end.isoformat()):
                frame = self.source.fetch_event_frame(start_date=block_start, end_date=block_end, row_limit=self.event_row_limit)
            self._raise_if_stopped()
            with self.profiler.stage("event_block_polars_to_numpy", block_index=block_index, rows=int(frame.height)):
                block = self.source.event_frame_to_block(frame=frame, start_date=block_start, end_date=block_end)
            self._raise_if_stopped()
            if block.row_count <= 0:
                cursor_date = block_end
                block_index += 1
                continue
            with self.profiler.stage(
                "event_block_append_engine",
                block_index=block_index,
                rows=block.row_count,
                tickers=block.ticker_count,
                min_timestamp_us=block.min_timestamp_us,
                max_timestamp_us=block.max_timestamp_us,
            ):
                self.engine.append_rows_by_ticker(block.rows_by_ticker)
            self._raise_if_stopped()
            if block.max_timestamp_us < self.start_timestamp_us:
                with self.profiler.stage("warmup_event_cache_trim", block_index=block_index):
                    self._trim_pre_start_event_cache()
                cursor_date = block_end
                block_index += 1
                continue
            if self.load_token_contexts:
                with self.profiler.stage("token_context_block_fetch", block_index=block_index):
                    context = self.source.fetch_token_contexts(
                        start_timestamp_us=max(self.start_timestamp_us, block.min_timestamp_us),
                        end_timestamp_us=min(self.end_timestamp_us + 1, block.max_timestamp_us + 1),
                        include_lookback=False,
                        include_xbrl=self.load_xbrl,
                    )
                self._raise_if_stopped()
                with self.profiler.stage("token_context_block_load", block_index=block_index, rows=context.row_count, counts=context.counts()):
                    self.engine.load_external_contexts(context.rows_by_context)
                self._raise_if_stopped()
            with self.profiler.stage("ready_index_build", block_index=block_index):
                ready_blocks = self.engine.build_ready_index_blocks(max_samples=self._ready_sample_cap(produced))
                ready_count = self.engine.ready_index_count(ready_blocks)
            self._raise_if_stopped()
            self.profiler.add(
                StreamingStageRecord(
                    stage="ready_index_count",
                    seconds=0.0,
                    rss_before_mib=current_rss_mib(),
                    rss_after_mib=current_rss_mib(),
                    metadata={"block_index": block_index, "samples": ready_count, "blocks": len(ready_blocks)},
                )
            )
            for sample_tuple in self.engine.iter_ready_sample_batches(batch_size=self.config.batch_size, blocks=ready_blocks):
                if self.max_batches > 0 and produced >= self.max_batches:
                    break
                eligible = tuple(
                    sample
                    for sample in sample_tuple
                    if self.start_timestamp_us <= int(sample.origin_timestamp_us) <= self.end_timestamp_us
                )
                self.engine.mark_processed(sample_tuple)
                if not eligible:
                    continue
                with self.profiler.stage("batch_materialize", block_index=block_index, batch_index=produced, samples=len(eligible)):
                    batch = self.engine.materialize_training_batch(eligible, batch_id=produced)
                if not self._put_queue_item(
                    StreamingBatchEnvelope(
                        batch=batch,
                        samples=eligible,
                        batch_index=produced,
                        block_index=block_index,
                    )
                ):
                    break
                produced += 1
            with self.profiler.stage("engine_trim_processed_tails", block_index=block_index):
                self.engine.trim_processed_tails()
            cursor_date = block_end
            block_index += 1
        return produced

    def _ready_sample_cap(self, produced_batches: int) -> int:
        configured = int(self.config.max_ready_samples)
        if self.max_batches <= 0:
            return configured
        remaining = max(0, int(self.max_batches) - int(produced_batches))
        batch_cap = remaining * int(self.config.batch_size)
        if configured > 0:
            return min(configured, batch_cap)
        return batch_cap

    def _trim_pre_start_event_cache(self) -> None:
        keep_tail = max(0, int(self.config.carryover_events))
        for ticker, rows in list(self.engine.rows_by_ticker.items()):
            if rows.size == 0:
                continue
            timestamps = rows["sip_timestamp_us"]
            pre_start_count = int(np.searchsorted(timestamps, self.start_timestamp_us, side="left"))
            trim_to = max(0, pre_start_count - keep_tail)
            if trim_to <= 0:
                continue
            self.engine.rows_by_ticker[ticker] = rows[trim_to:].copy()
            self.engine._processed_offsets[ticker] = 0

    def _raise_if_stopped(self) -> None:
        if self._stop_event.is_set():
            raise RuntimeError("Streaming rolling provider was stopped.")

    def _put_queue_item(self, item: Any) -> bool:
        while not self._stop_event.is_set():
            try:
                self.ready_queue.put(item, timeout=0.25)
                return True
            except queue.Full:
                continue
        return False

    def _try_put_sentinel(self) -> None:
        try:
            self.ready_queue.put_nowait(self._sentinel)
        except queue.Full:
            pass


def date_from_us(timestamp_us: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).date()


def parse_utc_us(value: str) -> int:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    else:
        parsed = parsed.astimezone(dt.timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def current_rss_mib() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process().memory_info().rss / (1024 * 1024))
    except Exception:
        return 0.0


def batch_nbytes(batch: RollingTrainingBatch) -> int:
    total = 0
    for value in (
        batch.headers_uint8,
        batch.events_uint8,
        batch.ticker,
        batch.origin_ordinal,
        batch.origin_timestamp_us,
        batch.ticker_macro_bars,
        batch.ticker_macro_bar_mask,
        batch.global_market_bars,
        batch.global_market_bar_mask,
        batch.future_macro_bars,
        batch.future_macro_bar_mask,
        batch.future_intraday_bars,
        batch.future_intraday_bar_mask,
    ):
        if isinstance(value, np.ndarray):
            total += int(value.nbytes)
    for group in (batch.time_features, batch.chunk_time_features, batch.macro_features, batch.global_features, batch.labels, batch.xbrl_inputs):
        for array in group.values():
            if isinstance(array, np.ndarray):
                total += int(array.nbytes)
    for text_group in batch.text_inputs.values():
        for array in text_group.values():
            if isinstance(array, np.ndarray):
                total += int(array.nbytes)
    return total


def batch_shape_summary(batch: RollingTrainingBatch) -> dict[str, Any]:
    return {
        "headers_uint8": list(batch.headers_uint8.shape),
        "events_uint8": list(batch.events_uint8.shape),
        "ticker_macro_bars": list(batch.ticker_macro_bars.shape),
        "global_market_bars": list(batch.global_market_bars.shape),
        "future_macro_bars": list(batch.future_macro_bars.shape),
        "future_intraday_bars": list(batch.future_intraday_bars.shape),
        "text_inputs": {
            name: {key: list(value.shape) for key, value in group.items() if isinstance(value, np.ndarray)}
            for name, group in batch.text_inputs.items()
        },
        "xbrl_inputs": {key: list(value.shape) for key, value in batch.xbrl_inputs.items() if isinstance(value, np.ndarray)},
    }


def _jsonable_record(record: StreamingStageRecord) -> dict[str, Any]:
    return {
        "stage": record.stage,
        "seconds": float(record.seconds),
        "rss_before_mib": float(record.rss_before_mib),
        "rss_after_mib": float(record.rss_after_mib),
        "rss_delta_mib": float(record.rss_after_mib - record.rss_before_mib),
        "metadata": _jsonable(record.metadata),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value
