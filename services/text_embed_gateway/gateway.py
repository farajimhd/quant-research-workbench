from __future__ import annotations

import asyncio
import gc
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pipelines.market_sip.events.clickhouse_build_text_tokens import (
    SourceBatch,
    TextEmbeddingModel,
    TextTokenizer,
    TokenTableBatch,
    create_news_embedding_table_sql,
    create_news_token_table_sql,
    create_sec_embedding_table_sql,
    create_sec_token_table_sql,
    embed_and_insert_token_table_batch,
    safe_div,
    sec_rendered_source_ctes_sql,
    tokenize_and_insert_source_batch,
)
from pipelines.sec.edgar.sec_pipeline.text_renderer import (
    STRUCTURED_XML_EXCLUDED_QUALITY_FLAG,
)
from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string
from services.gateway_core.market_calendar import MarketHoursSnapshot, MassiveMarketHoursClient
from services.news_gateway.run_logger import AsyncRunLogger
from services.text_embed_gateway.config import TextEmbedGatewayConfig


@dataclass(slots=True)
class TextEmbedMetrics:
    started_at_utc: str = field(default_factory=lambda: utc_now_text())
    current_phase: str = "starting"
    current_phase_message: str = "Starting text embedding gateway."
    current_phase_started_at_utc: str = field(default_factory=lambda: utc_now_text())
    model_status: str = "not_loaded"
    model_load_seconds: float = 0.0
    embedding_dim: int = 0
    embedding_device: str = ""
    embedding_torch_dtype: str = ""
    embedding_pooling: str = ""
    market_status: str = ""
    market_status_source: str = "local_clock"
    market_status_updated_at_utc: str = ""
    market_status_error: str = ""
    active_mode: str = ""
    active_source: str = ""
    active_stage: str = ""
    active_window_utc: str = ""
    active_detail: str = ""
    active_started_at_utc: str = ""
    next_poll_at_utc: str = ""
    next_poll_seconds: float = 0.0
    poll_cadence_reason: str = ""
    poll_cadence_label: str = ""
    last_embedding_at_utc: str = ""
    last_embedding_mode: str = ""
    last_embedding_source: str = ""
    last_embedding_stage: str = ""
    last_embedding_sequences: int = 0
    last_embedding_tokens: int = 0
    last_embedding_inference_seconds: float = 0.0
    last_embedding_insert_seconds: float = 0.0
    last_embedding_batch_seconds: float = 0.0
    last_embedding_sequences_per_second: float = 0.0
    last_embedding_tokens_per_second: float = 0.0
    cycles: int = 0
    live_cycles: int = 0
    historical_cycles: int = 0
    live_last_cycle_at_utc: str = ""
    historical_last_cycle_at_utc: str = ""
    live_last_cycle_seconds: float = 0.0
    historical_last_cycle_seconds: float = 0.0
    live_last_rows_written: int = 0
    historical_last_rows_written: int = 0
    live_last_gap_detected: int = 0
    historical_last_gap_detected: int = 0
    live_last_gap_completed: int = 0
    historical_last_gap_completed: int = 0
    live_last_gap_remaining: int = 0
    historical_last_gap_remaining: int = 0
    live_last_window_utc: str = ""
    historical_last_window_utc: str = ""
    source_rows_fetched: int = 0
    source_rows_tokenized: int = 0
    token_rows_fetched: int = 0
    embedding_rows_written: int = 0
    coverage_rows_written: int = 0
    news_source_rows: int = 0
    sec_source_rows: int = 0
    news_token_rows: int = 0
    sec_token_rows: int = 0
    last_cycle_seconds: float = 0.0
    last_fetch_seconds: float = 0.0
    last_embedding_seconds: float = 0.0
    last_insert_seconds: float = 0.0
    live_embedding_batches: int = 0
    historical_embedding_batches: int = 0
    live_embedding_sequences: int = 0
    historical_embedding_sequences: int = 0
    live_embedding_tokens: int = 0
    historical_embedding_tokens: int = 0
    live_embedding_seconds: float = 0.0
    historical_embedding_seconds: float = 0.0
    live_embedding_insert_seconds: float = 0.0
    historical_embedding_insert_seconds: float = 0.0
    live_embedding_batch_seconds: float = 0.0
    historical_embedding_batch_seconds: float = 0.0
    live_last_inference_seconds: float = 0.0
    historical_last_inference_seconds: float = 0.0
    live_last_inference_sequences: int = 0
    historical_last_inference_sequences: int = 0
    live_last_inference_tokens: int = 0
    historical_last_inference_tokens: int = 0
    live_last_batch_seconds: float = 0.0
    historical_last_batch_seconds: float = 0.0
    live_last_insert_seconds: float = 0.0
    historical_last_insert_seconds: float = 0.0
    active_queries: int = 0
    cancelled_queries: int = 0
    failures: int = 0
    last_error: str = ""
    last_error_status: str = ""
    last_error_seen_at_utc: str = ""
    last_error_resolved_at_utc: str = ""
    last_error_mode: str = ""
    last_error_source: str = ""
    last_processed_source: str = ""
    last_processed_mode: str = ""
    last_processed_rows: int = 0
    recent_status_rows: int = 0
    sec_bridge_table: str = ""
    sec_bridge_status: str = "not_checked"
    sec_context_table: str = ""
    sec_context_status: str = "not_checked"
    sec_context_rows_refreshed: int = 0
    gap_cycle_mode: str = ""
    gap_window_start_utc: str = ""
    gap_window_end_utc: str = ""
    gap_updated_at_utc: str = ""
    news_source_gap_detected: int = 0
    sec_source_gap_detected: int = 0
    news_token_gap_detected: int = 0
    sec_token_gap_detected: int = 0
    sec_context_gap_detected: int = 0
    sec_context_blocked_detected: int = 0
    news_source_gap_completed: int = 0
    sec_source_gap_completed: int = 0
    news_token_gap_completed: int = 0
    sec_token_gap_completed: int = 0
    sec_context_gap_completed: int = 0
    news_source_gap_remaining: int = 0
    sec_source_gap_remaining: int = 0
    news_token_gap_remaining: int = 0
    sec_token_gap_remaining: int = 0
    sec_context_gap_remaining: int = 0
    news_source_gap_period: str = ""
    sec_source_gap_period: str = ""
    news_token_gap_period: str = ""
    sec_token_gap_period: str = ""
    sec_context_gap_period: str = ""
    news_available_source_rows: int = 0
    sec_available_source_rows: int = 0
    news_available_token_rows: int = 0
    sec_available_token_rows: int = 0
    news_available_embedding_rows: int = 0
    sec_available_embedding_rows: int = 0
    news_available_period: str = ""
    sec_available_period: str = ""
    source_reports: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: {"live": {}, "historical": {}})
    run_log_path: str = ""


class TrackedClickHouseClient(ClickHouseHttpClient):
    def __init__(self, base_url: str, user: str, password: str, *, query_prefix: str) -> None:
        super().__init__(base_url, user, password)
        self.query_prefix = query_prefix
        self._active_query_ids: set[str] = set()
        self._lock = threading.RLock()

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        own_query_id = query_id or f"{self.query_prefix}_{uuid.uuid4().hex}"
        with self._lock:
            self._active_query_ids.add(own_query_id)
        try:
            return super().execute(sql, query_id=own_query_id)
        finally:
            with self._lock:
                self._active_query_ids.discard(own_query_id)

    def active_query_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._active_query_ids)

    def cancel_active_queries(self) -> int:
        query_ids = self.active_query_ids()
        if not query_ids:
            return 0
        values = ", ".join(sql_string(value) for value in query_ids)
        sql = f"KILL QUERY WHERE query_id IN ({values}) ASYNC"
        ClickHouseHttpClient.execute(self, sql, query_id=f"{self.query_prefix}_kill_{uuid.uuid4().hex}")
        return len(query_ids)


class TextEmbedGateway:
    def __init__(self, config: TextEmbedGatewayConfig) -> None:
        self.config = config
        self.metrics = TextEmbedMetrics()
        self._run_id = f"text_embed_gateway_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._terminal_task: asyncio.Task[None] | None = None
        password = default_password()
        self.client = TrackedClickHouseClient(config.clickhouse_url, config.clickhouse_user, password, query_prefix="text_embed")
        self.market_status_provider = MassiveMarketHoursClient.from_env(
            service_prefix="TEXT_EMBED",
            api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            status_url=config.market_status_url,
            holidays_url=config.market_holidays_url,
            enabled=config.market_status_enabled,
            refresh_seconds=config.market_status_refresh_seconds,
        )
        self.tokenizer: TextTokenizer | None = None
        self.embedding_model: TextEmbeddingModel | None = None
        self._sec_bridge_available = False
        self._recent: deque[dict[str, Any]] = deque(maxlen=max(1, config.recent_status_limit))
        self.logger = AsyncRunLogger(
            root=config.log_root_win,
            run_id=self._run_id,
            enabled=config.run_log_enabled,
            queue_size=config.run_log_queue_size,
        )
        self.logger.path = self.logger.path.with_name("text_embed_gateway_events.jsonl")
        self.metrics.run_log_path = str(self.logger.path) if config.run_log_enabled else ""
        self.report_path = config.log_root_win / self._run_id / "text_embed_gateway_profile.jsonl"
        self._profile_offset = self.report_path.stat().st_size if self.report_path.exists() else 0

    async def start(self) -> None:
        await self.logger.start()
        self._log("service_starting", config=self.config.public_dict())
        if self.config.terminal_rich_enabled and self._terminal_task is None:
            from services.text_embed_gateway.terminal import run_terminal_dashboard

            self._terminal_task = asyncio.create_task(run_terminal_dashboard(self), name="text-embed-terminal")
        try:
            self._set_phase("loading_model", "Loading tokenizer and Qwen embedding model to GPU.")
            await asyncio.to_thread(self._load_model)
            self._set_phase("schema", "Ensuring token, embedding, and coverage tables.")
            await asyncio.to_thread(self.ensure_schema)
            self._set_phase("polling", "Text embedding gap polling is running.")
            self._poll_task = asyncio.create_task(self._poll_loop(), name="text-embed-poll-loop")
            self._log("service_started")
        except Exception as exc:  # noqa: BLE001
            self.metrics.failures += 1
            self._record_error(repr(exc))
            self._set_phase("failed", repr(exc))
            self.logger.exception("service_start_failed", exc)
            raise

    async def stop(self) -> None:
        self._set_phase("stopping", "Shutdown requested; finishing current persist step and releasing GPU memory.")
        self._stop_event.set()
        try:
            cancelled = await asyncio.to_thread(self.client.cancel_active_queries)
            self.metrics.cancelled_queries += cancelled
            if cancelled:
                self._log("active_clickhouse_queries_cancelled", count=cancelled)
        except Exception as exc:  # noqa: BLE001
            self._record_error(repr(exc))
            self.logger.exception("cancel_active_queries_failed", exc)
        if self._poll_task is not None:
            try:
                await asyncio.wait_for(self._poll_task, timeout=self.config.graceful_shutdown_seconds)
            except TimeoutError:
                self._poll_task.cancel()
                await asyncio.gather(self._poll_task, return_exceptions=True)
                self._log("poll_loop_cancelled_after_timeout", timeout_seconds=self.config.graceful_shutdown_seconds)
        if self._terminal_task is not None:
            self._terminal_task.cancel()
            await asyncio.gather(self._terminal_task, return_exceptions=True)
        await asyncio.to_thread(self._release_model)
        await self.logger.stop()

    def snapshot_metrics(self) -> dict[str, Any]:
        self.metrics.active_queries = len(self.client.active_query_ids())
        self._prune_recent()
        self.metrics.recent_status_rows = len(self._recent)
        payload = asdict(self.metrics)
        for mode in ("live", "historical"):
            sequences = float(payload.get(f"{mode}_embedding_sequences") or 0.0)
            tokens = float(payload.get(f"{mode}_embedding_tokens") or 0.0)
            seconds = float(payload.get(f"{mode}_embedding_seconds") or 0.0)
            batches = float(payload.get(f"{mode}_embedding_batches") or 0.0)
            payload[f"{mode}_avg_inference_seconds"] = safe_div(seconds, batches)
            payload[f"{mode}_avg_inference_ms_per_sequence"] = 1000.0 * safe_div(seconds, sequences)
            payload[f"{mode}_avg_inference_sequences_per_second"] = safe_div(sequences, seconds)
            payload[f"{mode}_avg_inference_tokens_per_second"] = safe_div(tokens, seconds)
            payload[f"{mode}_avg_batch_seconds"] = safe_div(float(payload.get(f"{mode}_embedding_batch_seconds") or 0.0), batches)
            payload[f"{mode}_avg_insert_seconds"] = safe_div(float(payload.get(f"{mode}_embedding_insert_seconds") or 0.0), batches)
        return payload

    def recent_snapshot(self, limit: int = 50) -> dict[str, Any]:
        self._prune_recent()
        return {"rows": list(self._recent)[: max(1, int(limit))], "limit": limit}

    def current_market_status(self) -> MarketHoursSnapshot:
        status = self.market_status_provider.snapshot()
        self.metrics.market_status = status.session or status.market or "unknown"
        self.metrics.market_status_source = status.source
        self.metrics.market_status_updated_at_utc = status.checked_at_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.metrics.market_status_error = status.error
        return status

    def ensure_schema(self) -> None:
        args = self._args()
        statements = [
            f"CREATE DATABASE IF NOT EXISTS {quote_ident(self.config.target_database)}",
            create_news_token_table_sql(self.config.target_database, self.config.news_token_table, self.config.storage_policy),
            create_sec_token_table_sql(self.config.target_database, self.config.sec_token_table, self.config.storage_policy),
            create_news_embedding_table_sql(self.config.target_database, self.config.news_embedding_table, self.config.storage_policy),
            create_sec_embedding_table_sql(self.config.target_database, self.config.sec_embedding_table, self.config.storage_policy),
            create_coverage_table_sql(self.config.target_database, self.config.coverage_table, self.config.storage_policy),
            f"ALTER TABLE {quote_ident(self.config.target_database)}.{quote_ident(self.config.sec_token_table)} MODIFY COLUMN token_chunk_index UInt16",
            f"ALTER TABLE {quote_ident(self.config.target_database)}.{quote_ident(self.config.sec_embedding_table)} MODIFY COLUMN token_chunk_index UInt16",
            f"ALTER TABLE {quote_ident(self.config.target_database)}.{quote_ident(self.config.sec_token_table)} ADD COLUMN IF NOT EXISTS accepted_at_source LowCardinality(String) AFTER text_kind, ADD COLUMN IF NOT EXISTS event_time_quality LowCardinality(String) AFTER accepted_at_source",
            f"ALTER TABLE {quote_ident(self.config.target_database)}.{quote_ident(self.config.sec_embedding_table)} ADD COLUMN IF NOT EXISTS accepted_at_source LowCardinality(String) AFTER text_kind, ADD COLUMN IF NOT EXISTS event_time_quality LowCardinality(String) AFTER accepted_at_source",
            f"ALTER TABLE {quote_ident(self.config.target_database)}.{quote_ident(self.config.coverage_table)} MODIFY COLUMN token_chunk_index UInt16",
        ]
        for statement in statements:
            self.client.execute(statement)
        self._sec_bridge_available = self._resolve_sec_bridge_table()
        self.metrics.sec_bridge_table = f"{self.config.source_database}.{self.config.sec_bridge_table}"
        self.metrics.sec_bridge_status = "ready" if self._sec_bridge_available else "missing"
        self.metrics.sec_context_table = f"{self.config.source_database}.{self.config.sec_live_rendered_text_table}"
        self.metrics.sec_context_status = "direct_rendered_source"
        self._log("schema_ready", target_database=args.target_database)

    async def _poll_loop(self) -> None:
        last_historical_started = 0.0
        while not self._stop_event.is_set():
            started = time.perf_counter()
            try:
                status = await asyncio.to_thread(self.current_market_status)
                poll_seconds = self._poll_interval_seconds(status)
                self._set_phase("working", "Checking recent live text gaps.")
                await asyncio.to_thread(self._run_cycle, "live")

                now_monotonic = time.monotonic()
                should_run_historical = (
                    not status.active_collection_window
                    and now_monotonic - last_historical_started >= max(1.0, poll_seconds)
                )
                if should_run_historical and not self._stop_event.is_set():
                    last_historical_started = now_monotonic
                    self._set_phase("working", "Checking closed historical gaps.")
                    await asyncio.to_thread(self._run_cycle, "historical")

                self.metrics.last_cycle_seconds = time.perf_counter() - started
                self._set_waiting(status, poll_seconds)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.25, poll_seconds))
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.metrics.failures += 1
                self._record_error(repr(exc))
                self._set_phase("error", repr(exc))
                self.logger.exception("poll_cycle_failed", exc)
                try:
                    retry_seconds = min(30.0, max(1.0, self.config.closed_poll_seconds))
                    self._set_error_waiting(retry_seconds)
                    await asyncio.wait_for(self._stop_event.wait(), timeout=retry_seconds)
                except TimeoutError:
                    continue

    def _run_cycle(self, mode: str) -> None:
        cycle_started = time.perf_counter()
        self.metrics.cycles += 1
        if mode == "live":
            self.metrics.live_cycles += 1
        else:
            self.metrics.historical_cycles += 1
        ranges = self._time_ranges(mode)
        self._begin_gap_cycle(mode, ranges["news"])
        total_written = 0
        cycle_detected = 0
        cycle_completed = 0
        cycle_remaining = 0
        cycle_had_error = False
        for source in ("news", "sec"):
            if self._stop_event.is_set():
                break
            try:
                source_label = source.upper()
                mode_label_text = mode.upper()
                if source == "sec":
                    if not self._sec_bridge_available:
                        self.metrics.sec_bridge_status = "missing"
                        self._remember(source=source, mode=mode, stage="source_skipped_no_bridge", rows=0, seconds=0.0)
                        continue
                    self._set_active_work(mode=mode, source=source, stage="bridge_coverage", detail=f"{mode_label_text} SEC: checking rendered texts without an event-valid ticker mapping.", bounds=ranges[source])
                    blocked_rows = self._record_sec_context_blocks(ranges[source], mode)
                    self.metrics.sec_context_gap_detected = 0
                    self.metrics.sec_context_gap_completed = 0
                    self.metrics.sec_context_gap_remaining = 0
                    self.metrics.sec_context_blocked_detected = blocked_rows
                    self.metrics.sec_context_gap_period = ""
                    cycle_detected += blocked_rows
                    cycle_remaining += blocked_rows
                self._set_active_work(mode=mode, source=source, stage="source_gap_summary", detail=f"{mode_label_text} {source_label}: summarizing source-token gaps.", bounds=ranges[source])
                source_summary = self._summarize_source_gaps(source, ranges[source])
                cycle_detected += int(source_summary["rows"])
                self._set_active_work(mode=mode, source=source, stage="source_fetch", detail=f"{mode_label_text} {source_label}: fetching missing source text.", bounds=ranges[source])
                source_rows = self._fetch_missing_source_rows(source, ranges[source], mode)
                if source_rows:
                    self._set_active_work(mode=mode, source=source, stage="source_embed", detail=f"{mode_label_text} {source_label}: tokenizing and embedding {len(source_rows):,} source rows.", bounds=ranges[source])
                    total_written += self._tokenize_embed_and_persist_source(source, source_rows, mode)
                    self._set_gap_completed(source, "source", len(source_rows))
                if self._stop_event.is_set():
                    break
                self._set_active_work(mode=mode, source=source, stage="token_gap_summary", detail=f"{mode_label_text} {source_label}: summarizing token-embedding gaps.", bounds=ranges[source])
                token_summary = self._summarize_token_gaps(source, ranges[source])
                cycle_detected += int(token_summary["rows"])
                self._set_active_work(mode=mode, source=source, stage="token_fetch", detail=f"{mode_label_text} {source_label}: fetching token rows missing embeddings.", bounds=ranges[source])
                token_rows = self._fetch_missing_token_rows(source, ranges[source], mode)
                if token_rows:
                    self._set_active_work(mode=mode, source=source, stage="token_embed", detail=f"{mode_label_text} {source_label}: embedding {len(token_rows):,} existing token rows.", bounds=ranges[source])
                    total_written += self._embed_and_persist_tokens(source, token_rows, mode)
                    self._set_gap_completed(source, "token", len(token_rows))
                source_completed = int(getattr(self.metrics, f"{source}_source_gap_completed"))
                token_completed = int(getattr(self.metrics, f"{source}_token_gap_completed"))
                source_remaining = int(getattr(self.metrics, f"{source}_source_gap_remaining"))
                token_remaining = int(getattr(self.metrics, f"{source}_token_gap_remaining"))
                cycle_completed += source_completed + token_completed
                cycle_remaining += source_remaining + token_remaining
                self._set_active_work(mode=mode, source=source, stage="coverage_summary", detail=f"{mode_label_text} {source_label}: updating coverage report.", bounds=ranges[source])
                coverage_summary = self._summarize_available_coverage(source, ranges[source])
                self._record_source_report(
                    mode=mode,
                    source=source,
                    bounds=ranges[source],
                    coverage=coverage_summary,
                    source_detected=int(source_summary["rows"]),
                    source_completed=source_completed,
                    source_remaining=source_remaining,
                    source_period=gap_period(source_summary),
                    embedding_detected=int(token_summary["rows"]),
                    embedding_completed=token_completed,
                    embedding_remaining=token_remaining,
                    embedding_period=gap_period(token_summary),
                    context_detected=int(self.metrics.sec_context_gap_detected) if source == "sec" else 0,
                    context_completed=int(self.metrics.sec_context_gap_completed) if source == "sec" else 0,
                    context_remaining=int(self.metrics.sec_context_gap_remaining) if source == "sec" else 0,
                    context_blocked=int(self.metrics.sec_context_blocked_detected) if source == "sec" else 0,
                    context_period=str(self.metrics.sec_context_gap_period) if source == "sec" else "",
                )
                self._log(
                    "gap_summary",
                    mode=mode,
                    source=source,
                    source_available=coverage_summary["source_rows"],
                    embedding_input_available=coverage_summary["token_rows"],
                    embeddings_available=coverage_summary["embedding_rows"],
                    source_detected=source_summary["rows"],
                    source_completed=source_completed,
                    embedding_input_detected=token_summary["rows"],
                    embedding_input_completed=token_completed,
                    window_start=self.metrics.gap_window_start_utc,
                    window_end=self.metrics.gap_window_end_utc,
                )
            except Exception as exc:  # noqa: BLE001
                cycle_had_error = True
                self.metrics.failures += 1
                self._record_error(f"{source}: {exc!r}", mode=mode, source=source)
                self._remember(source=source, mode=mode, stage="error", rows=0, seconds=0.0)
                self.logger.exception("source_cycle_failed", exc, source=source, mode=mode)
        self._record_mode_cycle(
            mode=mode,
            rows=total_written,
            seconds=time.perf_counter() - cycle_started,
            detected=cycle_detected,
            completed=cycle_completed,
            remaining=cycle_remaining,
            bounds=ranges["news"],
        )
        self._log(
            "cycle_complete",
            mode=mode,
            rows=total_written,
            gaps_detected=cycle_detected,
            gaps_completed=cycle_completed,
            gaps_remaining=cycle_remaining,
            seconds=round(float(getattr(self.metrics, f"{'live' if mode == 'live' else 'historical'}_last_cycle_seconds")), 3),
        )
        if not cycle_had_error:
            self._resolve_last_error(reason=f"{mode}_cycle_completed", mode=mode)

    def _time_ranges(self, mode: str) -> dict[str, tuple[datetime, datetime]]:
        now = datetime.now(UTC)
        if mode == "live":
            start = now - timedelta(minutes=max(1, self.config.live_lookback_minutes))
            end = now + timedelta(minutes=5)
        else:
            start = now - timedelta(days=max(1, self.config.historical_lookback_days))
            end = now + timedelta(minutes=5)
        return {"news": (start, end), "sec": (start, end)}

    def _begin_gap_cycle(self, mode: str, bounds: tuple[datetime, datetime]) -> None:
        self.metrics.gap_cycle_mode = mode
        self.metrics.gap_window_start_utc = utc_text(bounds[0])
        self.metrics.gap_window_end_utc = utc_text(bounds[1])
        self.metrics.gap_updated_at_utc = utc_now_text()
        for name in (
            "news_source_gap_detected",
            "sec_source_gap_detected",
            "news_token_gap_detected",
            "sec_token_gap_detected",
            "sec_context_gap_detected",
            "sec_context_blocked_detected",
            "news_source_gap_completed",
            "sec_source_gap_completed",
            "news_token_gap_completed",
            "sec_token_gap_completed",
            "sec_context_gap_completed",
            "news_source_gap_remaining",
            "sec_source_gap_remaining",
            "news_token_gap_remaining",
            "sec_token_gap_remaining",
            "sec_context_gap_remaining",
        ):
            setattr(self.metrics, name, 0)
        for name in (
            "news_source_gap_period",
            "sec_source_gap_period",
            "news_token_gap_period",
            "sec_token_gap_period",
            "sec_context_gap_period",
        ):
            setattr(self.metrics, name, "")

    def _summarize_source_gaps(self, source: str, bounds: tuple[datetime, datetime]) -> dict[str, Any]:
        sql = news_source_gap_summary_sql(self.config, bounds) if source == "news" else sec_source_gap_summary_sql(self.config, bounds)
        summary = first_json_row(self.client.execute(sql))
        rows = int(summary.get("rows", 0) or 0)
        setattr(self.metrics, f"{source}_source_gap_detected", rows)
        setattr(self.metrics, f"{source}_source_gap_remaining", max(0, rows - int(getattr(self.metrics, f"{source}_source_gap_completed"))))
        setattr(self.metrics, f"{source}_source_gap_period", gap_period(summary))
        self.metrics.gap_updated_at_utc = utc_now_text()
        return summary

    def _summarize_token_gaps(self, source: str, bounds: tuple[datetime, datetime]) -> dict[str, Any]:
        sql = news_token_gap_summary_sql(self.config, bounds) if source == "news" else sec_token_gap_summary_sql(self.config, bounds)
        summary = first_json_row(self.client.execute(sql))
        rows = int(summary.get("rows", 0) or 0)
        setattr(self.metrics, f"{source}_token_gap_detected", rows)
        setattr(self.metrics, f"{source}_token_gap_remaining", max(0, rows - int(getattr(self.metrics, f"{source}_token_gap_completed"))))
        setattr(self.metrics, f"{source}_token_gap_period", gap_period(summary))
        self.metrics.gap_updated_at_utc = utc_now_text()
        return summary

    def _summarize_available_coverage(self, source: str, bounds: tuple[datetime, datetime]) -> dict[str, Any]:
        sql = news_available_coverage_sql(self.config, bounds) if source == "news" else sec_available_coverage_sql(self.config, bounds)
        summary = first_json_row(self.client.execute(sql))
        source_rows = int(summary.get("source_rows", 0) or 0)
        token_rows = int(summary.get("token_rows", 0) or 0)
        embedding_rows = int(summary.get("embedding_rows", 0) or 0)
        period = gap_period({"rows": source_rows, "min_time": summary.get("min_time"), "max_time": summary.get("max_time")})
        setattr(self.metrics, f"{source}_available_source_rows", source_rows)
        setattr(self.metrics, f"{source}_available_token_rows", token_rows)
        setattr(self.metrics, f"{source}_available_embedding_rows", embedding_rows)
        setattr(self.metrics, f"{source}_available_period", period)
        self._log(
            "coverage_summary",
            source=source,
            source_available=source_rows,
            embedding_input_available=token_rows,
            embeddings_available=embedding_rows,
            window_start=utc_text(bounds[0]),
            window_end=utc_text(bounds[1]),
            period=period,
        )
        return {"source_rows": source_rows, "token_rows": token_rows, "embedding_rows": embedding_rows, "period": period}

    def _set_gap_completed(self, source: str, kind: str, rows: int) -> None:
        completed_name = f"{source}_{kind}_gap_completed"
        detected_name = f"{source}_{kind}_gap_detected"
        remaining_name = f"{source}_{kind}_gap_remaining"
        completed = int(getattr(self.metrics, completed_name)) + int(rows)
        setattr(self.metrics, completed_name, completed)
        setattr(self.metrics, remaining_name, max(0, int(getattr(self.metrics, detected_name)) - completed))
        self.metrics.gap_updated_at_utc = utc_now_text()

    def _record_sec_context_blocks(self, bounds: tuple[datetime, datetime], mode: str) -> int:
        rows = json_rows(self.client.execute(sec_missing_bridge_rows_sql(self.config, bounds)))
        if not rows:
            return 0
        self._insert_coverage("sec", rows, mode=mode, stage="context", status="blocked_missing_ticker_mapping", token_rows=0, embedding_rows=0)
        self._remember(source="sec", mode=mode, stage="context_blocked_mapping", rows=len(rows), seconds=0.0)
        return len(rows)

    def _fetch_missing_source_rows(self, source: str, bounds: tuple[datetime, datetime], mode: str) -> list[dict[str, Any]]:
        started = time.perf_counter()
        sql = missing_news_source_sql(self.config, bounds, mode) if source == "news" else missing_sec_source_sql(self.config, bounds, mode)
        rows = json_rows(self.client.execute(sql))
        seconds = time.perf_counter() - started
        self.metrics.last_fetch_seconds = seconds
        self.metrics.source_rows_fetched += len(rows)
        if source == "news":
            self.metrics.news_source_rows += len(rows)
        else:
            self.metrics.sec_source_rows += len(rows)
        if rows:
            self._remember(source=source, mode=mode, stage="source_fetch", rows=len(rows), seconds=seconds)
        return rows

    def _fetch_missing_token_rows(self, source: str, bounds: tuple[datetime, datetime], mode: str) -> list[dict[str, Any]]:
        started = time.perf_counter()
        sql = missing_news_token_sql(self.config, bounds, mode) if source == "news" else missing_sec_token_sql(self.config, bounds, mode)
        rows = json_rows(self.client.execute(sql))
        seconds = time.perf_counter() - started
        self.metrics.last_fetch_seconds = seconds
        self.metrics.token_rows_fetched += len(rows)
        if source == "news":
            self.metrics.news_token_rows += len(rows)
        else:
            self.metrics.sec_token_rows += len(rows)
        if rows:
            self._remember(source=source, mode=mode, stage="token_fetch", rows=len(rows), seconds=seconds)
        return rows

    def _tokenize_embed_and_persist_source(self, source: str, rows: list[dict[str, Any]], mode: str) -> int:
        if self.tokenizer is None or self.embedding_model is None:
            raise RuntimeError("Text embedding model is not loaded.")
        started = time.perf_counter()
        profile_offset = self._profile_offset
        source_batch = SourceBatch(source=source, rows=rows, seconds=0.0)
        before = self.metrics.embedding_rows_written
        token_rows = tokenize_and_insert_source_batch(
            self.client,
            self.tokenizer,
            self.embedding_model,
            self._args(),
            source_batch,
            report_path=self.report_path,
        )
        self.metrics.source_rows_tokenized += len(rows)
        written = int(token_rows)
        self.metrics.embedding_rows_written += written
        self.metrics.last_embedding_seconds = time.perf_counter() - started
        self._record_embedding_profile(mode=mode, source=source, stage="source_embed", rows=self._consume_profile_rows(profile_offset))
        self._insert_coverage(source, rows, mode=mode, stage="source", status="ready", token_rows=token_rows, embedding_rows=written)
        self._remember(source=source, mode=mode, stage="source_embed", rows=written, seconds=self.metrics.last_embedding_seconds)
        self.metrics.last_processed_source = source
        self.metrics.last_processed_mode = mode
        self.metrics.last_processed_rows = written
        return self.metrics.embedding_rows_written - before

    def _embed_and_persist_tokens(self, source: str, rows: list[dict[str, Any]], mode: str) -> int:
        if self.embedding_model is None:
            raise RuntimeError("Text embedding model is not loaded.")
        started = time.perf_counter()
        profile_offset = self._profile_offset
        before = self.metrics.embedding_rows_written
        inserted = embed_and_insert_token_table_batch(
            self.client,
            self.embedding_model,
            self._args(),
            TokenTableBatch(source=source, rows=rows, seconds=0.0),
            report_path=self.report_path,
        )
        self.metrics.embedding_rows_written += inserted
        self.metrics.last_embedding_seconds = time.perf_counter() - started
        self._record_embedding_profile(mode=mode, source=source, stage="token_embed", rows=self._consume_profile_rows(profile_offset))
        self._insert_coverage(source, rows, mode=mode, stage="token", status="ready", token_rows=len(rows), embedding_rows=inserted)
        self._remember(source=source, mode=mode, stage="token_embed", rows=inserted, seconds=self.metrics.last_embedding_seconds)
        self.metrics.last_processed_source = source
        self.metrics.last_processed_mode = mode
        self.metrics.last_processed_rows = inserted
        return self.metrics.embedding_rows_written - before

    def _insert_coverage(self, source: str, rows: list[dict[str, Any]], *, mode: str, stage: str, status: str, token_rows: int, embedding_rows: int) -> None:
        if not rows:
            return
        started = time.perf_counter()
        payload = []
        for row in rows:
            event_time_key = "published_at_utc" if source == "news" else "accepted_at_utc"
            payload.append(
                {
                    "source": source,
                    "mode": mode,
                    "stage": stage,
                    "status": status,
                    "ticker": str(row.get("ticker", "") or "").upper(),
                    "timestamp_us": int(row.get("timestamp_us", 0) or 0),
                    "event_time": str(row.get(event_time_key, "") or ""),
                    "source_id": str(row.get("source_id", "") or ""),
                    "token_chunk_index": int(row.get("token_chunk_index", 0) or 0),
                    "tokenizer_model": self.config.tokenizer_model,
                    "embedding_model": self.config.embedding_model,
                    "embedding_pooling": self.config.embedding_pooling,
                    "token_rows": int(token_rows),
                    "embedding_rows": int(embedding_rows),
                    "last_error": "",
                }
            )
        target = f"{quote_ident(self.config.target_database)}.{quote_ident(self.config.coverage_table)}"
        insert_json_each_row(self.client, target, payload)
        self.metrics.coverage_rows_written += len(payload)
        self.metrics.last_insert_seconds = time.perf_counter() - started

    def _consume_profile_rows(self, offset: int) -> list[dict[str, Any]]:
        if not self.report_path.exists():
            self._profile_offset = 0
            return []
        current_size = self.report_path.stat().st_size
        if current_size < offset:
            offset = 0
        rows: list[dict[str, Any]] = []
        with self.report_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    rows.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
            self._profile_offset = handle.tell()
        return rows

    def _record_embedding_profile(self, *, mode: str, source: str, stage: str, rows: list[dict[str, Any]]) -> None:
        if mode not in {"live", "historical"}:
            mode = "historical"
        if not rows:
            return
        batches = len(rows)
        sequences = sum(int(row.get("embedding_sequences", row.get("embedding_rows", 0)) or 0) for row in rows)
        tokens = sum(int(row.get("embedding_tokens", 0) or 0) for row in rows)
        inference_seconds = sum(float(row.get("embedding_seconds", 0.0) or 0.0) for row in rows)
        insert_seconds = sum(float(row.get("embedding_insert_seconds", 0.0) or 0.0) for row in rows)
        batch_seconds = sum(float(row.get("batch_seconds", 0.0) or 0.0) for row in rows)
        setattr(self.metrics, f"{mode}_embedding_batches", int(getattr(self.metrics, f"{mode}_embedding_batches")) + batches)
        setattr(self.metrics, f"{mode}_embedding_sequences", int(getattr(self.metrics, f"{mode}_embedding_sequences")) + sequences)
        setattr(self.metrics, f"{mode}_embedding_tokens", int(getattr(self.metrics, f"{mode}_embedding_tokens")) + tokens)
        setattr(self.metrics, f"{mode}_embedding_seconds", float(getattr(self.metrics, f"{mode}_embedding_seconds")) + inference_seconds)
        setattr(self.metrics, f"{mode}_embedding_insert_seconds", float(getattr(self.metrics, f"{mode}_embedding_insert_seconds")) + insert_seconds)
        setattr(self.metrics, f"{mode}_embedding_batch_seconds", float(getattr(self.metrics, f"{mode}_embedding_batch_seconds")) + batch_seconds)
        setattr(self.metrics, f"{mode}_last_inference_seconds", inference_seconds)
        setattr(self.metrics, f"{mode}_last_inference_sequences", sequences)
        setattr(self.metrics, f"{mode}_last_inference_tokens", tokens)
        setattr(self.metrics, f"{mode}_last_batch_seconds", batch_seconds)
        setattr(self.metrics, f"{mode}_last_insert_seconds", insert_seconds)
        self.metrics.last_embedding_at_utc = utc_now_text()
        self.metrics.last_embedding_mode = mode
        self.metrics.last_embedding_source = source
        self.metrics.last_embedding_stage = stage
        self.metrics.last_embedding_sequences = sequences
        self.metrics.last_embedding_tokens = tokens
        self.metrics.last_embedding_inference_seconds = inference_seconds
        self.metrics.last_embedding_insert_seconds = insert_seconds
        self.metrics.last_embedding_batch_seconds = batch_seconds
        self.metrics.last_embedding_sequences_per_second = safe_div(sequences, inference_seconds)
        self.metrics.last_embedding_tokens_per_second = safe_div(tokens, inference_seconds)
        self._remember(source=source, mode=mode, stage=f"{stage}_timing", rows=sequences, seconds=inference_seconds)
        self._log(
            "embedding_timing",
            mode=mode,
            source=source,
            stage=stage,
            batches=batches,
            sequences=sequences,
            tokens=tokens,
            inference_seconds=round(inference_seconds, 6),
            batch_seconds=round(batch_seconds, 6),
            insert_seconds=round(insert_seconds, 6),
            sequences_per_second=safe_div(sequences, inference_seconds),
            tokens_per_second=safe_div(tokens, inference_seconds),
        )

    def _record_mode_cycle(
        self,
        *,
        mode: str,
        rows: int,
        seconds: float,
        detected: int,
        completed: int,
        remaining: int,
        bounds: tuple[datetime, datetime],
    ) -> None:
        if mode not in {"live", "historical"}:
            mode = "historical"
        setattr(self.metrics, f"{mode}_last_cycle_at_utc", utc_now_text())
        setattr(self.metrics, f"{mode}_last_cycle_seconds", float(seconds))
        setattr(self.metrics, f"{mode}_last_rows_written", int(rows))
        setattr(self.metrics, f"{mode}_last_gap_detected", int(detected))
        setattr(self.metrics, f"{mode}_last_gap_completed", int(completed))
        setattr(self.metrics, f"{mode}_last_gap_remaining", int(remaining))
        setattr(self.metrics, f"{mode}_last_window_utc", f"{utc_text(bounds[0])} -> {utc_text(bounds[1])}")

    def _record_source_report(
        self,
        *,
        mode: str,
        source: str,
        bounds: tuple[datetime, datetime],
        coverage: dict[str, Any],
        source_detected: int,
        source_completed: int,
        source_remaining: int,
        source_period: str,
        embedding_detected: int,
        embedding_completed: int,
        embedding_remaining: int,
        embedding_period: str,
        context_detected: int = 0,
        context_completed: int = 0,
        context_remaining: int = 0,
        context_blocked: int = 0,
        context_period: str = "",
    ) -> None:
        if mode not in {"live", "historical"}:
            mode = "historical"
        report = {
            "updated_at_utc": utc_now_text(),
            "window_start_utc": utc_text(bounds[0]),
            "window_end_utc": utc_text(bounds[1]),
            "available_source_rows": int(coverage.get("source_rows", 0) or 0),
            "available_token_rows": int(coverage.get("token_rows", 0) or 0),
            "available_embedding_rows": int(coverage.get("embedding_rows", 0) or 0),
            "available_period": str(coverage.get("period", "") or ""),
            "source_detected": int(source_detected),
            "source_completed": int(source_completed),
            "source_remaining": int(source_remaining),
            "source_period": str(source_period or ""),
            "embedding_detected": int(embedding_detected),
            "embedding_completed": int(embedding_completed),
            "embedding_remaining": int(embedding_remaining),
            "embedding_period": str(embedding_period or ""),
            "context_detected": int(context_detected),
            "context_completed": int(context_completed),
            "context_remaining": int(context_remaining),
            "context_blocked": int(context_blocked),
            "context_period": str(context_period or ""),
        }
        reports = self.metrics.source_reports
        reports.setdefault(mode, {})[source] = report

    def _load_model(self) -> None:
        started = time.perf_counter()
        try:
            self.tokenizer = TextTokenizer(model=self.config.tokenizer_model, local_files_only=self.config.local_files_only, strict=True)
            self.embedding_model = TextEmbeddingModel(
                model=self.config.embedding_model,
                tokenizer_model=self.config.tokenizer_model,
                local_files_only=self.config.local_files_only,
                device=self.config.embedding_device,
                torch_dtype=self.config.embedding_torch_dtype,
                pooling=self.config.embedding_pooling,
            )
        except Exception as exc:  # noqa: BLE001
            hint = (
                "Qwen files are not available in the local HuggingFace cache. "
                "Run once with '.\\scripts\\run_text_embed_gateway.ps1 -LoadModelCheck -NoLocalFilesOnly' "
                "or set TEXT_EMBED_LOCAL_FILES_ONLY=false on a machine with internet access. "
                "After the files are cached, return to the default offline mode for production."
            )
            raise RuntimeError(f"{hint} Original error: {exc!r}") from exc
        self.metrics.model_status = "loaded"
        self.metrics.model_load_seconds = time.perf_counter() - started
        self.metrics.embedding_dim = int(self.embedding_model.embedding_dim)
        self.metrics.embedding_device = self.embedding_model.device
        self.metrics.embedding_torch_dtype = self.embedding_model.torch_dtype_name
        self.metrics.embedding_pooling = self.embedding_model.pooling

    def _resolve_sec_bridge_table(self) -> bool:
        database = self.config.source_database
        table = self.config.sec_bridge_table
        rows = self.client.execute(
            f"""
            SELECT database
            FROM system.tables
            WHERE name = {sql_string(table)}
              AND database = {sql_string(database)}
            ORDER BY database
            LIMIT 1
            FORMAT TSV
            """
        ).strip().splitlines()
        available = bool(rows and rows[0].strip())
        if not available:
            self._log("sec_bridge_table_missing", database=database, table=table)
        else:
            self._log("sec_bridge_table_resolved", database=database, table=table)
        return available

    def _release_model(self) -> None:
        self._set_phase("releasing_model", "Freeing tokenizer/model references and CUDA cache.")
        torch_module = getattr(self.embedding_model, "torch", None)
        self.embedding_model = None
        self.tokenizer = None
        gc.collect()
        if torch_module is not None and torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
            try:
                torch_module.cuda.ipc_collect()
            except Exception:
                pass
        self.metrics.model_status = "released"

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(
            target_database=self.config.target_database,
            news_token_table=self.config.news_token_table,
            sec_token_table=self.config.sec_token_table,
            news_embedding_table=self.config.news_embedding_table,
            sec_embedding_table=self.config.sec_embedding_table,
            tokenizer_model=self.config.tokenizer_model,
            embedding_model=self.config.embedding_model,
            embedding_pooling=self.config.embedding_pooling,
            news_max_tokens=self.config.news_max_tokens,
            news_max_chunks=self.config.news_max_chunks,
            sec_chunk_tokens=self.config.sec_chunk_tokens,
            sec_max_chunks=self.config.sec_max_chunks,
            insert_batch_size=max(1, self.config.source_batch_size),
            embedding_batch_size=max(1, self.config.embedding_batch_size),
            embedding_insert_batch_size=max(1, self.config.embedding_insert_batch_size),
            dry_run=False,
            build_embeddings=True,
            max_threads=self.config.max_threads,
            max_memory_usage=self.config.max_memory_usage,
        )

    def _set_phase(self, phase: str, message: str) -> None:
        self.metrics.current_phase = phase
        self.metrics.current_phase_message = message
        self.metrics.current_phase_started_at_utc = utc_now_text()
        self._log("phase", phase=phase, message=message)

    def _set_active_work(self, *, mode: str, source: str, stage: str, detail: str, bounds: tuple[datetime, datetime] | None = None) -> None:
        self.metrics.active_mode = mode
        self.metrics.active_source = source
        self.metrics.active_stage = stage
        self.metrics.active_detail = detail
        self.metrics.active_started_at_utc = utc_now_text()
        self.metrics.active_window_utc = f"{utc_text(bounds[0])} -> {utc_text(bounds[1])}" if bounds is not None else ""
        self.metrics.next_poll_at_utc = ""
        self.metrics.next_poll_seconds = 0.0
        self.metrics.poll_cadence_reason = ""
        self._set_phase("working", detail)

    def _set_waiting(self, status: MarketHoursSnapshot, sleep_seconds: float) -> None:
        now = datetime.now(UTC)
        self.metrics.active_mode = ""
        self.metrics.active_source = ""
        self.metrics.active_stage = "waiting"
        self.metrics.active_detail = f"Sleeping before next live gap check ({self._poll_cadence_reason(status)})."
        self.metrics.active_started_at_utc = utc_text(now)
        self.metrics.active_window_utc = ""
        self.metrics.next_poll_seconds = float(sleep_seconds)
        self.metrics.next_poll_at_utc = utc_text(now + timedelta(seconds=max(0.25, sleep_seconds)))
        self.metrics.poll_cadence_reason = self._poll_cadence_reason(status)
        self.metrics.poll_cadence_label = self._poll_cadence_label(status)
        self._set_phase("waiting", self.metrics.active_detail)

    def _set_error_waiting(self, sleep_seconds: float) -> None:
        now = datetime.now(UTC)
        self.metrics.active_mode = ""
        self.metrics.active_source = ""
        self.metrics.active_stage = "error_backoff"
        self.metrics.active_detail = "Backing off after poll cycle error."
        self.metrics.active_started_at_utc = utc_text(now)
        self.metrics.active_window_utc = ""
        self.metrics.next_poll_seconds = float(sleep_seconds)
        self.metrics.next_poll_at_utc = utc_text(now + timedelta(seconds=max(0.25, sleep_seconds)))
        self.metrics.poll_cadence_reason = "error_backoff"
        self.metrics.poll_cadence_label = f"{sleep_seconds:.0f}s error backoff"

    def _record_error(self, message: str, *, mode: str = "", source: str = "") -> None:
        self.metrics.last_error = str(message)
        self.metrics.last_error_status = "active"
        self.metrics.last_error_seen_at_utc = utc_now_text()
        self.metrics.last_error_resolved_at_utc = ""
        self.metrics.last_error_mode = mode
        self.metrics.last_error_source = source

    def _resolve_last_error(self, *, reason: str, mode: str = "", source: str = "") -> None:
        if not self.metrics.last_error or self.metrics.last_error_status == "resolved":
            return
        if self.metrics.last_error_mode and mode != self.metrics.last_error_mode:
            return
        if self.metrics.last_error_source and source and source != self.metrics.last_error_source:
            return
        self.metrics.last_error_status = "resolved"
        self.metrics.last_error_resolved_at_utc = utc_now_text()
        self._log("last_error_resolved", reason=reason, last_error=self.metrics.last_error)

    def _poll_interval_seconds(self, status: MarketHoursSnapshot) -> float:
        if status.active_collection_window:
            return max(0.25, self.config.live_poll_seconds)
        if self._is_weekend_status(status):
            return max(1.0, self.config.weekend_poll_seconds)
        return max(1.0, self.config.closed_poll_seconds)

    def _poll_cadence_label(self, status: MarketHoursSnapshot) -> str:
        seconds = self._poll_interval_seconds(status)
        if status.active_collection_window:
            return f"{seconds:.0f}s active"
        if self._is_weekend_status(status):
            return f"{seconds:.0f}s weekend"
        return f"{seconds:.0f}s closed"

    def _poll_cadence_reason(self, status: MarketHoursSnapshot) -> str:
        if status.active_collection_window:
            return "active market collection window"
        if self._is_weekend_status(status):
            return "weekend closed market"
        return "closed market"

    def _is_weekend_status(self, status: MarketHoursSnapshot) -> bool:
        try:
            return datetime.fromisoformat(status.local_time_et).weekday() >= 5
        except ValueError:
            return datetime.now(UTC).astimezone().weekday() >= 5

    def _remember(self, *, source: str, mode: str, stage: str, rows: int, seconds: float) -> None:
        self._recent.appendleft(
            {
                "updated_at_utc": utc_now_text(),
                "source": source,
                "mode": mode,
                "stage": stage,
                "rows": int(rows),
                "seconds": round(float(seconds), 3),
            }
        )
        self._prune_recent()

    def _prune_recent(self) -> None:
        retention = timedelta(hours=max(0.0, self.config.recent_status_retention_hours))
        if retention.total_seconds() <= 0:
            return
        cutoff = datetime.now(UTC) - retention
        self._recent = deque((row for row in self._recent if parse_utc(row.get("updated_at_utc")) >= cutoff), maxlen=max(1, self.config.recent_status_limit))

    def _log(self, event: str, **payload: Any) -> None:
        self.logger.event(event, **payload)


def missing_news_source_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime], mode: str) -> str:
    limit = config.source_batch_size if mode == "live" else config.historical_batch_limit
    db = quote_ident(config.source_database)
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.news_token_table)
    body_chars = max(1, config.news_body_prefix_chars)
    external_chars = max(1, config.news_external_prefix_chars)
    pdf_chars = max(1, config.news_pdf_prefix_chars)
    return f"""
SELECT
    nt.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(nt.published_at_utc)) AS timestamp_us,
    nt.published_at_utc AS published_at_utc,
    nt.canonical_news_id AS source_id,
    nt.provider AS provider,
    nt.provider_article_id AS provider_article_id,
    n.title AS title,
    n.teaser AS teaser,
    substring(n.body_text, 1, {body_chars}) AS body_text,
    substring(n.external_text, 1, {external_chars}) AS external_text,
    substring(n.pdf_text, 1, {pdf_chars}) AS pdf_text,
    length(n.body_text) AS body_text_char_count,
    length(n.external_text) AS external_text_char_count,
    length(n.pdf_text) AS pdf_text_char_count,
    length(n.body_text) + length(n.external_text) + length(n.pdf_text) AS source_text_char_count,
    n.has_body AS has_body,
    n.has_external_text AS has_external_text,
    n.has_pdf AS has_pdf,
    n.article_url AS article_url,
    n.url_domain AS url_domain,
    arrayStringConcat(n.channels, ',') AS channels,
    arrayStringConcat(n.provider_tags, ',') AS provider_tags,
    arrayStringConcat(n.content_quality_flags, ',') AS quality_flags
FROM {db}.benzinga_news_ticker_v1 AS nt
ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
    ON nt.canonical_news_id = n.canonical_news_id
LEFT JOIN {target}.{token_table} AS tok
    ON tok.ticker = nt.ticker
   AND tok.source_id = nt.canonical_news_id
   AND tok.tokenizer_model = {sql_string(config.tokenizer_model)}
WHERE nt.published_at_utc >= {dt64_sql(bounds[0])}
  AND nt.published_at_utc < {dt64_sql(bounds[1])}
  AND tok.source_id = ''
ORDER BY nt.published_at_utc DESC, nt.ticker, nt.canonical_news_id
LIMIT {max(1, int(limit))}
{query_settings(config)}
FORMAT JSONEachRow
"""


def news_source_gap_summary_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    db = quote_ident(config.source_database)
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.news_token_table)
    return f"""
SELECT
    count() AS rows,
    min(nt.published_at_utc) AS min_time,
    max(nt.published_at_utc) AS max_time
FROM {db}.benzinga_news_ticker_v1 AS nt
ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
    ON nt.canonical_news_id = n.canonical_news_id
LEFT JOIN {target}.{token_table} AS tok
    ON tok.ticker = nt.ticker
   AND tok.source_id = nt.canonical_news_id
   AND tok.tokenizer_model = {sql_string(config.tokenizer_model)}
WHERE nt.published_at_utc >= {dt64_sql(bounds[0])}
  AND nt.published_at_utc < {dt64_sql(bounds[1])}
  AND tok.source_id = ''
{query_settings(config)}
FORMAT JSONEachRow
"""


def news_available_coverage_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    db = quote_ident(config.source_database)
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.news_token_table)
    embedding_table = quote_ident(config.news_embedding_table)
    return f"""
SELECT
    (
        SELECT count()
        FROM {db}.benzinga_news_ticker_v1 AS nt
        ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
            ON nt.canonical_news_id = n.canonical_news_id
        WHERE nt.published_at_utc >= {dt64_sql(bounds[0])}
          AND nt.published_at_utc < {dt64_sql(bounds[1])}
    ) AS source_rows,
    (
        SELECT count()
        FROM {target}.{token_table} AS t
        WHERE t.published_at_utc >= {dt64_sql(bounds[0])}
          AND t.published_at_utc < {dt64_sql(bounds[1])}
          AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
    ) AS token_rows,
    (
        SELECT count()
        FROM {target}.{embedding_table} AS e
        WHERE e.published_at_utc >= {dt64_sql(bounds[0])}
          AND e.published_at_utc < {dt64_sql(bounds[1])}
          AND e.embedding_model = {sql_string(config.embedding_model)}
          AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
    ) AS embedding_rows,
    (
        SELECT min(nt.published_at_utc)
        FROM {db}.benzinga_news_ticker_v1 AS nt
        ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
            ON nt.canonical_news_id = n.canonical_news_id
        WHERE nt.published_at_utc >= {dt64_sql(bounds[0])}
          AND nt.published_at_utc < {dt64_sql(bounds[1])}
    ) AS min_time,
    (
        SELECT max(nt.published_at_utc)
        FROM {db}.benzinga_news_ticker_v1 AS nt
        ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
            ON nt.canonical_news_id = n.canonical_news_id
        WHERE nt.published_at_utc >= {dt64_sql(bounds[0])}
          AND nt.published_at_utc < {dt64_sql(bounds[1])}
    ) AS max_time
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_missing_bridge_rows_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    limit = max(1, int(config.source_batch_size))
    source_db = quote_ident(config.source_database)
    filing_table = quote_ident(config.sec_live_filing_table)
    document_table = quote_ident(config.sec_live_document_table)
    rendered_table = quote_ident(config.sec_live_rendered_text_table)
    return f"""
WITH {sec_bridge_cte_sql(config)}
SELECT
    '' AS ticker,
    toUInt64(toUnixTimestamp64Micro(f.accepted_at_utc)) AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    concat(f.accession_number, ':', toString(toUInt8(least(toUInt32(d.sequence_number), 255))), ':', r.document_id) AS source_id,
    f.cik AS cik,
    f.accession_number AS accession_number,
    ifNull(f.form_type, '') AS form_type,
    toUInt8(least(toUInt32(d.sequence_number), 255)) AS text_rank,
    r.document_id AS document_id,
    ifNull(r.text_kind, '') AS text_kind
FROM {source_db}.{rendered_table} AS r FINAL
INNER JOIN {source_db}.{document_table} AS d FINAL
    ON d.document_id = r.document_id
   AND d.cik = r.cik
   AND d.accession_number = r.accession_number
INNER JOIN {source_db}.{filing_table} AS f FINAL
    ON f.cik = r.cik
   AND f.accession_number = r.accession_number
LEFT JOIN bridge AS b
    ON b.cik = f.cik
   AND (b.accession_number = '' OR b.accession_number = f.accession_number)
   AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
   AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
WHERE f.accepted_at_utc >= {dt64_sql(bounds[0])}
  AND f.accepted_at_utc < {dt64_sql(bounds[1])}
  AND b.cik = ''
  AND notEmpty(r.text)
ORDER BY f.accepted_at_utc DESC, f.cik, f.accession_number, r.text_kind, r.document_id
LIMIT {limit}
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_bridge_cte_sql(config: TextEmbedGatewayConfig) -> str:
    source_db = quote_ident(config.source_database)
    bridge_table = quote_ident(config.sec_bridge_table)
    return f"""
bridge AS
(
    SELECT
        ifNull(ticker, '') AS ticker,
        cik,
        ifNull(accession_number, '') AS accession_number,
        valid_from_date,
        valid_to_date_exclusive,
        any(bridge_id) AS bridge_id,
        any(ifNull(security_id, '')) AS security_id,
        any(ifNull(listing_id, '')) AS listing_id,
        any(ifNull(symbol_id, '')) AS symbol_id,
        max(confidence_score) AS confidence_score
    FROM {source_db}.{bridge_table}
    WHERE ifNull(ticker, '') != ''
      AND mapping_status IN ('active', 'mapped', 'accepted', '')
    GROUP BY ticker, cik, accession_number, valid_from_date, valid_to_date_exclusive
)
"""


def missing_sec_source_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime], mode: str) -> str:
    limit = config.source_batch_size if mode == "live" else config.historical_batch_limit
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.sec_token_table)
    return f"""
WITH {sec_direct_source_ctes_sql(config, bounds)}
SELECT
    src.ticker,
    src.timestamp_us,
    src.accepted_at_utc,
    concat(src.accession_number, ':', toString(src.text_rank), ':', src.document_id) AS source_id,
    src.cik,
    src.accession_number,
    src.form_type,
    src.text_rank,
    src.document_id,
    src.text_kind,
    src.text AS text,
    src.source_text_char_count AS source_text_char_count,
    toUInt8(0) AS text_prefix_truncated,
    src.quality_flags
FROM sec_rendered_source AS src
LEFT JOIN {target}.{token_table} AS tok
    ON tok.ticker = src.ticker
   AND tok.accession_number = src.accession_number
   AND tok.text_rank = src.text_rank
   AND tok.document_id = src.document_id
   AND tok.tokenizer_model = {sql_string(config.tokenizer_model)}
WHERE tok.source_id = ''
  AND src.accepted_at_utc >= {dt64_sql(bounds[0])}
  AND src.accepted_at_utc < {dt64_sql(bounds[1])}
  AND positionCaseInsensitive(ifNull(src.quality_flags, ''), {sql_string(STRUCTURED_XML_EXCLUDED_QUALITY_FLAG)}) = 0
ORDER BY src.accepted_at_utc DESC, src.ticker, src.accession_number, src.text_rank, src.document_id
LIMIT {max(1, int(limit))}
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_source_gap_summary_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.sec_token_table)
    return f"""
WITH {sec_direct_source_ctes_sql(config, bounds)}
SELECT
    count() AS rows,
    min(src.accepted_at_utc) AS min_time,
    max(src.accepted_at_utc) AS max_time
FROM sec_rendered_source AS src
LEFT JOIN {target}.{token_table} AS tok
    ON tok.ticker = src.ticker
   AND tok.accession_number = src.accession_number
   AND tok.text_rank = src.text_rank
   AND tok.document_id = src.document_id
   AND tok.tokenizer_model = {sql_string(config.tokenizer_model)}
WHERE tok.source_id = ''
  AND src.accepted_at_utc >= {dt64_sql(bounds[0])}
  AND src.accepted_at_utc < {dt64_sql(bounds[1])}
  AND positionCaseInsensitive(ifNull(src.quality_flags, ''), {sql_string(STRUCTURED_XML_EXCLUDED_QUALITY_FLAG)}) = 0
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_available_coverage_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.sec_token_table)
    embedding_table = quote_ident(config.sec_embedding_table)
    return f"""
WITH {sec_direct_source_ctes_sql(config, bounds)}
SELECT
    (
        SELECT count()
        FROM sec_rendered_source AS src
    ) AS source_rows,
    (
        SELECT count()
        FROM {target}.{token_table} AS t
        WHERE t.accepted_at_utc >= {dt64_sql(bounds[0])}
          AND t.accepted_at_utc < {dt64_sql(bounds[1])}
          AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
    ) AS token_rows,
    (
        SELECT count()
        FROM {target}.{embedding_table} AS e
        WHERE e.accepted_at_utc >= {dt64_sql(bounds[0])}
          AND e.accepted_at_utc < {dt64_sql(bounds[1])}
          AND e.embedding_model = {sql_string(config.embedding_model)}
          AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
    ) AS embedding_rows,
    (
        SELECT min(src.accepted_at_utc)
        FROM sec_rendered_source AS src
    ) AS min_time,
    (
        SELECT max(src.accepted_at_utc)
        FROM sec_rendered_source AS src
    ) AS max_time
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_direct_source_ctes_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    return sec_rendered_source_ctes_sql(
        source_database=config.source_database,
        filing_table=config.sec_live_filing_table,
        document_table=config.sec_live_document_table,
        rendered_text_table=config.sec_live_rendered_text_table,
        bridge_table=config.sec_bridge_table,
        start_sql=dt64_sql(bounds[0]),
        end_sql=dt64_sql(bounds[1]),
    )


def missing_news_token_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime], mode: str) -> str:
    limit = config.token_batch_size if mode == "live" else config.historical_batch_limit
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.news_token_table)
    embedding_table = quote_ident(config.news_embedding_table)
    return f"""
SELECT
    t.ticker, t.timestamp_us, t.published_at_utc, t.source_id, t.provider, t.provider_article_id,
    t.title, t.article_url, t.url_domain, t.channels, t.provider_tags, t.quality_flags,
    t.tokenizer_model, t.max_tokens, t.token_chunk_index, t.token_start, t.token_end,
    t.original_token_count, t.token_count, t.padding_tokens, t.was_truncated,
    t.input_ids, t.attention_mask, t.text_hash, t.text_char_count, t.source_text_char_count,
    t.text_prefix_truncated
FROM {target}.{token_table} AS t
LEFT JOIN {target}.{embedding_table} AS e
    ON e.ticker = t.ticker
   AND e.source_id = t.source_id
   AND e.token_chunk_index = t.token_chunk_index
   AND e.embedding_model = {sql_string(config.embedding_model)}
   AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
WHERE t.published_at_utc >= {dt64_sql(bounds[0])}
  AND t.published_at_utc < {dt64_sql(bounds[1])}
  AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
  AND e.source_id = ''
ORDER BY t.published_at_utc DESC, t.ticker, t.source_id, t.token_chunk_index
LIMIT {max(1, int(limit))}
{query_settings(config)}
FORMAT JSONEachRow
"""


def news_token_gap_summary_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.news_token_table)
    embedding_table = quote_ident(config.news_embedding_table)
    return f"""
SELECT
    count() AS rows,
    min(t.published_at_utc) AS min_time,
    max(t.published_at_utc) AS max_time
FROM {target}.{token_table} AS t
LEFT JOIN {target}.{embedding_table} AS e
    ON e.ticker = t.ticker
   AND e.source_id = t.source_id
   AND e.token_chunk_index = t.token_chunk_index
   AND e.embedding_model = {sql_string(config.embedding_model)}
   AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
WHERE t.published_at_utc >= {dt64_sql(bounds[0])}
  AND t.published_at_utc < {dt64_sql(bounds[1])}
  AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
  AND e.source_id = ''
{query_settings(config)}
FORMAT JSONEachRow
"""


def missing_sec_token_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime], mode: str) -> str:
    limit = config.token_batch_size if mode == "live" else config.historical_batch_limit
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.sec_token_table)
    embedding_table = quote_ident(config.sec_embedding_table)
    return f"""
SELECT
    t.ticker, t.timestamp_us, t.accepted_at_utc, t.source_id, t.cik, t.accession_number,
    t.form_type, t.text_rank, t.document_id, t.text_kind, t.quality_flags, t.tokenizer_model,
    t.max_tokens, t.token_chunk_index, t.token_start, t.token_end, t.original_token_count,
    t.token_count, t.padding_tokens, t.was_truncated, t.input_ids, t.attention_mask,
    t.text_hash, t.text_char_count, t.source_text_char_count, t.text_prefix_truncated
FROM {target}.{token_table} AS t
LEFT JOIN {target}.{embedding_table} AS e
    ON e.ticker = t.ticker
   AND e.accession_number = t.accession_number
   AND e.text_rank = t.text_rank
   AND e.document_id = t.document_id
   AND e.source_id = t.source_id
   AND e.token_chunk_index = t.token_chunk_index
   AND e.embedding_model = {sql_string(config.embedding_model)}
   AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
WHERE t.accepted_at_utc >= {dt64_sql(bounds[0])}
  AND t.accepted_at_utc < {dt64_sql(bounds[1])}
  AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
  AND e.source_id = ''
ORDER BY t.accepted_at_utc DESC, t.ticker, t.accession_number, t.text_rank, t.document_id, t.token_chunk_index
LIMIT {max(1, int(limit))}
{query_settings(config)}
FORMAT JSONEachRow
"""


def sec_token_gap_summary_sql(config: TextEmbedGatewayConfig, bounds: tuple[datetime, datetime]) -> str:
    target = quote_ident(config.target_database)
    token_table = quote_ident(config.sec_token_table)
    embedding_table = quote_ident(config.sec_embedding_table)
    return f"""
SELECT
    count() AS rows,
    min(t.accepted_at_utc) AS min_time,
    max(t.accepted_at_utc) AS max_time
FROM {target}.{token_table} AS t
LEFT JOIN {target}.{embedding_table} AS e
    ON e.ticker = t.ticker
   AND e.accession_number = t.accession_number
   AND e.text_rank = t.text_rank
   AND e.document_id = t.document_id
   AND e.source_id = t.source_id
   AND e.token_chunk_index = t.token_chunk_index
   AND e.embedding_model = {sql_string(config.embedding_model)}
   AND e.embedding_pooling = {sql_string(config.embedding_pooling)}
WHERE t.accepted_at_utc >= {dt64_sql(bounds[0])}
  AND t.accepted_at_utc < {dt64_sql(bounds[1])}
  AND t.tokenizer_model = {sql_string(config.tokenizer_model)}
  AND e.source_id = ''
{query_settings(config)}
FORMAT JSONEachRow
"""


def create_coverage_table_sql(database: str, table: str, storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    source LowCardinality(String),
    mode LowCardinality(String),
    stage LowCardinality(String),
    status LowCardinality(String),
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    event_time DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    source_id String,
    token_chunk_index UInt16,
    tokenizer_model LowCardinality(String),
    embedding_model LowCardinality(String),
    embedding_pooling LowCardinality(String),
    token_rows UInt32,
    embedding_rows UInt32,
    last_error String CODEC(ZSTD(3)),
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(event_time)
ORDER BY (source, ticker, timestamp_us, source_id, token_chunk_index)
SETTINGS {", ".join(settings)}
"""


def insert_json_each_row(client: ClickHouseHttpClient, table: str, rows: list[dict[str, Any]], *, columns: str = "") -> None:
    payload = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in rows)
    if payload:
        column_sql = f" ({columns})" if columns else ""
        client.execute(f"INSERT INTO {table}{column_sql} SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n{payload}")


def json_rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def first_json_row(text: str) -> dict[str, Any]:
    rows = json_rows(text)
    return rows[0] if rows else {"rows": 0, "min_time": "", "max_time": ""}


def gap_period(summary: dict[str, Any]) -> str:
    rows = int(summary.get("rows", 0) or 0)
    if rows <= 0:
        return ""
    start = compact_utc_value(summary.get("min_time"))
    end = compact_utc_value(summary.get("max_time"))
    if not start and not end:
        return ""
    if start == end:
        return start
    return f"{start} -> {end}"


def compact_utc_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("1970-01-01"):
        return ""
    return text.replace("T", " ").replace("+00:00", "").replace("Z", "")[:19]


def dt64_sql(value: datetime) -> str:
    value = value.astimezone(UTC)
    text = value.isoformat(timespec="microseconds").replace("+00:00", "")
    return f"toDateTime64({sql_string(text)}, 9, 'UTC')"


def query_settings(config: TextEmbedGatewayConfig) -> str:
    settings = []
    if int(config.max_threads) > 0:
        settings.append(f"max_threads = {int(config.max_threads)}")
    if config.max_memory_usage.strip() and config.max_memory_usage.strip() != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(config.max_memory_usage)}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now_clickhouse_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.fromtimestamp(0, UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def default_password() -> str:
    from research.mlops.clickhouse import default_clickhouse_password

    return default_clickhouse_password()
