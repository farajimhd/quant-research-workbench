from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipelines.news.benzinga.core.coverage_manifest import (
    CoverageGap,
    CoverageInterval,
    CoverageManifestConfig,
    CoverageSnapshot,
    CoverageBootstrapSummary,
    bootstrap_coverage_from_normalized_table,
    compact_coverage_manifest,
    ensure_coverage_manifest_table,
    find_coverage_gaps,
    insert_coverage_snapshot,
    load_coverage_intervals,
    new_run_id,
    parse_clickhouse_datetime,
)
from pipelines.news.benzinga.news_benzinga_normalize import artifact_path_for_payload, parse_provider_datetime, write_raw_payload
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline, ProcessedNewsItem
from pipelines.news.benzinga.news_pipeline.provider import BenzingaProviderClient, BenzingaProviderConfig
from research.mlops.clickhouse import ClickHouseHttpClient
from services.news_gateway.config import (
    NewsGatewayConfig,
    WORKSTATION_CODE_ROOT_WIN,
    WORKSTATION_DATA_ROOT_WIN,
    WORKSTATION_SHARE_CODE_ROOT_WIN,
    WORKSTATION_SHARE_DATA_ROOT_WIN,
    default_clickhouse_password,
)
from services.news_gateway.preflight import PreflightError, PreflightReport, run_preflight
from services.news_gateway.run_logger import AsyncRunLogger
from services.news_gateway.state import NewsMemoryState


EASTERN = ZoneInfo("America/New_York")


@dataclass(slots=True)
class GatewayMetrics:
    started_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    poll_runs: int = 0
    poll_failures: int = 0
    provider_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    written_rows: int = 0
    skipped_existing: int = 0
    raw_saved: int = 0
    last_poll_at_utc: str = ""
    last_error: str = ""
    gap_status: str = "not_started"
    gap_message: str = ""
    manual_gap_fill_command: str = ""
    manual_gap_fill_script_win: str = ""
    manual_gap_fill_manifest_win: str = ""
    last_cycle_status: str = ""
    last_cycle_provider_rows: int = 0
    last_cycle_processed_rows: int = 0
    last_cycle_written_rows: int = 0
    last_cycle_skipped_existing: int = 0
    last_cycle_wall_seconds: float = 0.0
    current_poll_seconds: float = 0.0
    current_phase: str = "starting"
    current_phase_message: str = "Starting news gateway."
    current_phase_started_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    preflight_status: str = "not_started"
    preflight_checked_at_utc: str = ""
    preflight_checks: list[dict[str, Any]] = field(default_factory=list)
    run_log_path: str = ""
    bootstrap_probe_total: int = 0
    bootstrap_probe_completed: int = 0
    bootstrap_probe_empty: int = 0
    bootstrap_probe_positive: int = 0
    gap_fill_total_chunks: int = 0
    gap_fill_flushed_chunks: int = 0
    gap_fill_submitted_chunks: int = 0
    gap_fill_in_flight_chunks: int = 0
    publish_status: str = "idle"
    publish_active_jobs: int = 0
    publish_pending_rows: int = 0
    publish_completed_jobs: int = 0
    publish_failed_jobs: int = 0
    publish_last_message: str = ""


@dataclass(frozen=True, slots=True)
class GapFillInterval:
    start_utc: datetime
    end_utc: datetime


@dataclass(slots=True)
class GapCoverageRun:
    coverage_id: str
    started_at_utc: datetime
    start_utc: datetime
    end_utc: datetime
    chunk_count: int = 0
    provider_rows: int = 0
    processed_rows: int = 0
    written_rows: int = 0
    skipped_existing: int = 0
    pages: int = 0


@dataclass(frozen=True, slots=True)
class GapFillChunk:
    index: int
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class GapFillChunkOutcome:
    chunk: GapFillChunk
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ManualGapFillPlan:
    script_path: Path
    manifest_path: Path
    workstation_script_path: Path
    workstation_manifest_path: Path
    intervals: list[GapFillInterval]


class NewsGateway:
    def __init__(self, config: NewsGatewayConfig) -> None:
        self.config = config
        self.state = NewsMemoryState(config.recent_history_limit)
        self.metrics = GatewayMetrics()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._gap_task: asyncio.Task[None] | None = None
        self._terminal_task: asyncio.Task[None] | None = None
        self._publish_tasks: set[asyncio.Task[Any]] = set()
        self._publish_task_rows: dict[asyncio.Task[Any], int] = {}
        self._preflight_report: PreflightReport | None = None
        self._run_id = new_run_id("news_gateway")
        self._live_coverage_id = f"{self._run_id}_live"
        self._live_coverage_started_at = datetime.now(UTC)
        self._live_coverage_start: datetime | None = None
        self._live_coverage_end: datetime | None = None
        self._live_coverage_poll_runs = 0
        self._live_coverage_provider_rows = 0
        self._live_coverage_processed_rows = 0
        self._live_coverage_written_rows = 0
        self._live_coverage_failed_rows = 0
        self._live_coverage_skipped_existing = 0
        self._gap_coverage_counter = 0
        self._bootstrap_probe_count = 0
        self._bootstrap_probe_empty = 0
        self._bootstrap_probe_positive = 0
        self._clickhouse_password = default_clickhouse_password()
        self._massive_api_key = massive_api_key()
        self.logger = AsyncRunLogger(
            root=config.log_root_win,
            run_id=self._run_id,
            enabled=config.run_log_enabled,
            queue_size=config.run_log_queue_size,
        )
        self.metrics.run_log_path = str(self.logger.path) if config.run_log_enabled else ""
        self.pipeline = BenzingaNewsPipeline(
            BenzingaPipelineConfig(
                policy_json=config.policy_json,
                text_limit_chars=config.text_limit_chars,
                raw_root_win=config.raw_root_win,
                output_root_win=config.prepared_root_win / "benzinga_news_gateway",
            )
        )
        self.target = ClickHouseTargetConfig(
            url=config.clickhouse_url,
            user=config.clickhouse_user,
            password=self._clickhouse_password,
            database=config.clickhouse_database,
            normalized_table=config.normalized_table,
            ticker_table=config.ticker_table,
            coverage_table=config.coverage_table,
        )
        self.provider = BenzingaProviderClient(
            BenzingaProviderConfig(
                endpoint_url=config.benzinga_url,
                api_key=self._massive_api_key,
                page_limit=config.page_limit,
                max_pages=config.max_pages,
            )
        )

    async def start(self) -> None:
        await self.logger.start()
        self._log_event("service_starting", config=self.config.public_dict())
        try:
            self._set_phase("preflight", "Checking ClickHouse, artifact storage, and Benzinga provider access.")
            await self.preflight()
            self._set_phase("coverage_bootstrap", "Preparing hourly coverage manifest from existing normalized news rows.")
            await self._prepare_coverage_manifest()
            self._set_phase("gap_planning", "Loading coverage intervals and planning startup gap handling.")
            await self._plan_startup_gap()
            self._set_phase("live_coverage", "Opening live coverage manifest row.")
            await self._open_live_coverage()
            self._set_phase("polling", "Live news polling is running.")
            self._poll_task = asyncio.create_task(self._poll_loop(), name="benzinga-news-poll-loop")
            if self.config.terminal_rich_enabled:
                from services.news_gateway.terminal import run_terminal_dashboard

                self._terminal_task = asyncio.create_task(run_terminal_dashboard(self), name="benzinga-news-terminal-dashboard")
            self._log_event("service_started")
        except Exception as exc:
            self.logger.exception("service_start_failed", exc)
            await self.logger.stop()
            raise

    async def stop(self) -> None:
        self._log_event("service_stopping")
        self._stop_event.set()
        await self._wait_for_service_tasks_to_quiesce()
        await self._drain_publish_tasks("shutdown")
        tasks = [task for task in [self._poll_task, self._gap_task, self._terminal_task] if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._drain_publish_tasks("post_cancel")
        await self._close_live_coverage()
        self._log_event("service_stopped", metrics=self.snapshot_metrics())
        await self.logger.stop()

    async def preflight(self) -> PreflightReport:
        try:
            report = await asyncio.to_thread(
                run_preflight,
                self.config,
                clickhouse_password=self._clickhouse_password,
                api_key=self._massive_api_key,
            )
        except PreflightError as exc:
            self._record_preflight_report(exc.report)
            self.metrics.last_error = repr(exc)
            self.logger.exception("preflight_failed", exc, report=exc.report.public_dict())
            raise
        self._record_preflight_report(report)
        self._log_event("preflight_completed", report=report.public_dict())
        return report

    def _record_preflight_report(self, report: PreflightReport) -> None:
        self._preflight_report = report
        self.metrics.preflight_status = report.status
        self.metrics.preflight_checked_at_utc = report.checked_at_utc
        self.metrics.preflight_checks = [asdict(check) for check in report.checks]

    def _set_phase(self, phase: str, message: str) -> None:
        self.metrics.current_phase = phase
        self.metrics.current_phase_message = message
        self.metrics.current_phase_started_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._log_event("phase_changed", phase=phase, message=message)
        self._print_status(f"news_gateway_phase={phase} message={message}")

    async def _plan_startup_gap(self) -> None:
        now = datetime.now(UTC)
        intervals = await asyncio.to_thread(self._load_coverage_intervals)
        if not intervals:
            self.metrics.gap_status = "no_watermark"
            self.metrics.gap_message = "No coverage manifest intervals found; live polling will use normal lookback."
            self._log_event("startup_gap_plan", status=self.metrics.gap_status, message=self.metrics.gap_message)
            return
        gaps = find_coverage_gaps(
            intervals,
            end_utc=now,
            merge_tolerance_seconds=max(0, self.config.poll_overlap_seconds),
            trailing_live_lookback_seconds=0,
        )
        if not gaps:
            latest_end = max(interval.end_utc for interval in intervals)
            self.metrics.gap_status = "covered_by_live_lookback"
            self.metrics.gap_message = f"Coverage manifest is current enough; latest coverage ends at {latest_end.isoformat()}."
            self._log_event("startup_gap_plan", status=self.metrics.gap_status, message=self.metrics.gap_message)
            return
        largest_gap_seconds = max(gap.seconds for gap in gaps)
        total_gap_seconds = sum(gap.seconds for gap in gaps)
        threshold_seconds = self.config.startup_auto_fill_max_gap_days * 86_400
        unique_gap_days = count_unique_utc_days_for_gaps(gaps)
        if total_gap_seconds <= threshold_seconds:
            self.metrics.gap_status = "auto_started"
            self.metrics.gap_message = (
                f"{len(gaps)} coverage gap(s), {unique_gap_days} unique UTC day(s), "
                f"{total_gap_seconds / 3600:.1f} total empty hour(s), will be filled in background during startup."
            )
            self._log_event("startup_gap_plan", status=self.metrics.gap_status, gaps=len(gaps), unique_gap_days=unique_gap_days, total_gap_seconds=total_gap_seconds)
            self._print_status(self.metrics.gap_message)
            self._gap_task = asyncio.create_task(self._fill_gaps(gaps), name="benzinga-news-startup-gap-fill")
            return
        gap_intervals = [GapFillInterval(gap.start_utc, gap.end_utc) for gap in gaps]
        plan = await asyncio.to_thread(write_manual_gap_fill_plan, gap_intervals, self.config)
        self.metrics.manual_gap_fill_command = historical_gap_command(gaps[0].start_utc, gaps[0].end_utc, self.config)
        self.metrics.manual_gap_fill_script_win = str(plan.workstation_script_path)
        self.metrics.manual_gap_fill_manifest_win = str(plan.workstation_manifest_path)
        if self.config.is_workstation:
            self.metrics.gap_status = "workstation_auto_started_large_gap"
            self.metrics.gap_message = (
                f"{len(gaps)} coverage gap(s) found; {unique_gap_days} unique UTC day(s), "
                f"{total_gap_seconds / 3600:.1f} total empty hour(s), largest {largest_gap_seconds / 3600:.1f} hour(s). "
                "Running the generated workstation gap-fill script: "
                f"{plan.workstation_script_path}"
            )
            self._log_event(
                "startup_gap_plan",
                status=self.metrics.gap_status,
                gaps=len(gaps),
                unique_gap_days=unique_gap_days,
                total_gap_seconds=total_gap_seconds,
                script=str(plan.workstation_script_path),
                manifest=str(plan.workstation_manifest_path),
            )
            self._print_status(self.metrics.gap_message)
            self._print_status(f"manifest: {plan.workstation_manifest_path}")
            self._gap_task = asyncio.create_task(self._run_workstation_gap_fill_plan(plan), name="benzinga-news-workstation-gap-fill")
            return
        self.metrics.gap_status = "manual_required_large_gap"
        self.metrics.gap_message = (
            f"{len(gaps)} coverage gap(s) found; {unique_gap_days} unique UTC day(s), "
            f"{total_gap_seconds / 3600:.1f} total empty hour(s), largest {largest_gap_seconds / 3600:.1f} hour(s). "
            "Run the generated script on the workstation: "
            f"{plan.workstation_script_path}"
        )
        self._log_event(
            "startup_gap_plan",
            status=self.metrics.gap_status,
            gaps=len(gaps),
            unique_gap_days=unique_gap_days,
            total_gap_seconds=total_gap_seconds,
            script=str(plan.workstation_script_path),
            manifest=str(plan.workstation_manifest_path),
        )
        self._print_status(self.metrics.gap_message)
        self._print_status(f"manifest: {plan.workstation_manifest_path}")

    async def _fill_gaps(self, gaps: list[CoverageGap]) -> None:
        total = len(gaps)
        for index, gap in enumerate(gaps, start=1):
            if self._stop_event.is_set():
                break
            self.metrics.gap_status = "auto_running"
            self.metrics.gap_message = (
                f"Filling startup coverage gap {index}/{total}: "
                f"{gap.start_utc.isoformat()} -> {gap.end_utc.isoformat()} ({gap.seconds / 60:.1f} minutes)."
            )
            self._set_phase("gap_fill", self.metrics.gap_message)
            self._print_status(self.metrics.gap_message)
            await self._fill_gap(gap.start_utc, gap.end_utc)
        self.metrics.gap_status = "auto_completed"
        self.metrics.gap_message = f"Startup coverage gap fill completed for {total} gap(s)."

    async def _fill_gap(self, start_utc: datetime, end_utc: datetime) -> None:
        chunks = build_gap_fill_chunks(start_utc, end_utc, self.config.gap_fill_chunk_minutes)
        workers = max(1, self.config.startup_gap_fill_workers)
        self.metrics.gap_fill_total_chunks = len(chunks)
        self.metrics.gap_fill_flushed_chunks = 0
        self.metrics.gap_fill_submitted_chunks = 0
        self.metrics.gap_fill_in_flight_chunks = 0
        self.metrics.gap_message = (
            f"Concurrent startup gap fill: {len(chunks):,} chunk(s), workers={workers}, "
            f"range={start_utc.isoformat()}->{end_utc.isoformat()}."
        )
        self._set_phase("gap_fill_concurrent", self.metrics.gap_message)
        self._log_event(
            "gap_fill_started",
            start_utc=start_utc,
            end_utc=end_utc,
            chunks=len(chunks),
            workers=workers,
            chunk_minutes=self.config.gap_fill_chunk_minutes,
        )
        coverage_run: GapCoverageRun | None = None
        next_submit = 0
        next_flush = 0
        completed: dict[int, GapFillChunkOutcome] = {}
        pending: set[asyncio.Task[GapFillChunkOutcome]] = set()
        try:
            while next_flush < len(chunks) and not self._stop_event.is_set():
                while next_submit < len(chunks) and len(pending) < workers:
                    chunk = chunks[next_submit]
                    pending.add(asyncio.create_task(self._fill_gap_chunk(chunk), name=f"benzinga-gap-chunk-{chunk.index}"))
                    next_submit += 1
                    self.metrics.gap_fill_submitted_chunks = next_submit
                    self.metrics.gap_fill_in_flight_chunks = len(pending)
                if not pending:
                    break
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    outcome = task.result()
                    completed[outcome.chunk.index] = outcome
                self.metrics.gap_fill_in_flight_chunks = len(pending)
                while next_flush in completed:
                    outcome = completed.pop(next_flush)
                    result = outcome.result
                    if result.get("status") == "ok":
                        coverage_run = self._extend_gap_coverage_run(
                            coverage_run,
                            outcome.chunk.start_utc,
                            outcome.chunk.end_utc,
                            result,
                        )
                        await asyncio.to_thread(self._write_gap_coverage_run, coverage_run, "running", None)
                    else:
                        if coverage_run is not None:
                            await asyncio.to_thread(self._write_gap_coverage_run, coverage_run, "completed", datetime.now(UTC))
                            coverage_run = None
                    next_flush += 1
                    self.metrics.gap_fill_flushed_chunks = next_flush
                    self.metrics.gap_fill_submitted_chunks = next_submit
                    self.metrics.gap_fill_in_flight_chunks = len(pending)
                    self.metrics.gap_message = (
                        f"Startup gap fill progress: flushed={next_flush:,}/{len(chunks):,}, "
                        f"submitted={next_submit:,}, in_flight={len(pending):,}."
                    )
                    self._set_phase("gap_fill_progress", self.metrics.gap_message)
                    self._log_event("gap_fill_progress", flushed=next_flush, total_chunks=len(chunks), submitted=next_submit, in_flight=len(pending))
        except asyncio.CancelledError:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        finally:
            if coverage_run is not None:
                await asyncio.to_thread(self._write_gap_coverage_run, coverage_run, "completed", datetime.now(UTC))
            self.metrics.gap_fill_flushed_chunks = next_flush
            self.metrics.gap_fill_submitted_chunks = next_submit
            self.metrics.gap_fill_in_flight_chunks = len(pending)
            self._log_event("gap_fill_finished", start_utc=start_utc, end_utc=end_utc, flushed=next_flush, total_chunks=len(chunks))

    async def _fill_gap_chunk(self, chunk: GapFillChunk) -> GapFillChunkOutcome:
        if self._stop_event.is_set():
            return GapFillChunkOutcome(chunk=chunk, result={"status": "stopped"})
        result = await self.poll_window(chunk.start_utc, chunk.end_utc, coverage_mode="gap_fill_deferred")
        return GapFillChunkOutcome(chunk=chunk, result=result)

    async def _run_workstation_gap_fill_plan(self, plan: ManualGapFillPlan) -> None:
        script_path = str(plan.workstation_script_path)
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                self.metrics.gap_message = line
                self._print_status(line)
        return_code = await process.wait()
        if return_code == 0:
            self.metrics.gap_status = "auto_completed"
            self.metrics.gap_message = f"Workstation gap-fill script completed: {script_path}"
        else:
            self.metrics.gap_status = "failed"
            self.metrics.gap_message = f"Workstation gap-fill script failed with exit code {return_code}: {script_path}"
            self.metrics.last_error = self.metrics.gap_message

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            end_utc = datetime.now(UTC)
            start_utc = end_utc - timedelta(minutes=max(1, self.config.lookback_minutes))
            await self.poll_window(start_utc, end_utc, coverage_mode="live")
            sleep_seconds = self.current_poll_seconds()
            self.metrics.current_poll_seconds = sleep_seconds
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_seconds)
            except TimeoutError:
                pass

    async def poll_window(self, start_utc: datetime, end_utc: datetime, *, coverage_mode: str = "live") -> dict[str, Any]:
        started = time.perf_counter()
        self.metrics.poll_runs += 1
        self.metrics.last_poll_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        poll_id = f"{coverage_mode}_{self.metrics.poll_runs:012d}_{uuid.uuid4().hex[:8]}"
        self._log_event("poll_started", poll_id=poll_id, coverage_mode=coverage_mode, start_utc=start_utc, end_utc=end_utc)
        try:
            self._set_phase(f"{coverage_mode}_fetch", f"Fetching Benzinga news {start_utc.isoformat()} -> {end_utc.isoformat()}.")
            fetch_result = await asyncio.to_thread(self.provider.fetch_window, start_utc, end_utc)
            self._log_event(
                "provider_fetch_completed",
                poll_id=poll_id,
                provider_rows=len(fetch_result.items),
                pages=fetch_result.pages,
                saturated=fetch_result.saturated,
            )
            self._set_phase(f"{coverage_mode}_process", f"Processing {len(fetch_result.items):,} provider row(s).")
            processed: list[ProcessedNewsItem] = []
            failed = 0
            for payload in fetch_result.items:
                try:
                    raw_path, raw_hash = await asyncio.to_thread(save_raw_payload, self.config.raw_root_win, payload)
                    self.metrics.raw_saved += 1
                    item = self.pipeline.process_payload(
                        payload,
                        raw_artifact_path=str(raw_path),
                        raw_payload_hash=raw_hash,
                        downloaded_at_utc=datetime.now(UTC),
                    )
                    processed.append(item)
                    self._log_event(
                        "item_processed",
                        poll_id=poll_id,
                        provider_article_id=item.result.provider_article_id,
                        canonical_news_id=item.result.canonical_news_id,
                        published_at_utc=item.result.normalized_row.get("published_at_utc"),
                        ticker_count=len(item.result.ticker_links),
                        fetch_task_count=len(item.result.url_resolution.fetch_tasks),
                        warning_count=len(item.result.warnings),
                        content_quality_flags=item.result.normalized_row.get("content_quality_flags"),
                        raw_artifact_path=str(raw_path),
                        raw_payload_hash=raw_hash,
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.metrics.last_error = repr(exc)
                    self.logger.exception(
                        "item_processing_failed",
                        exc,
                        poll_id=poll_id,
                        provider_article_id=str(payload.get("id") or payload.get("article_id") or ""),
                    )
            write_summary = await self._publish_processed(processed, poll_id=poll_id, coverage_mode=coverage_mode)
            skip_sample_size = max(0, self.config.run_log_skip_sample_size)
            skipped_ids = list(getattr(write_summary, "skipped_existing_ids", []) or [])
            duplicate_ids = list(getattr(write_summary, "input_duplicate_ids", []) or [])
            self._set_phase(
                f"{coverage_mode}_write",
                f"Wrote {write_summary.normalized_rows_inserted:,} row(s), skipped {write_summary.skipped_existing:,}.",
            )
            await self.state.add_rows([item.result.normalized_row for item in processed])
            self.metrics.provider_rows += len(fetch_result.items)
            self.metrics.processed_rows += len(processed)
            self.metrics.failed_rows += failed
            self.metrics.written_rows += write_summary.normalized_rows_inserted
            self.metrics.skipped_existing += write_summary.skipped_existing
            if fetch_result.saturated:
                self.metrics.last_error = "Benzinga provider response saturated; coverage was not advanced for this window."
            self.metrics.last_cycle_status = "ok" if failed == 0 and not fetch_result.saturated else "completed_with_errors"
            self.metrics.last_cycle_provider_rows = len(fetch_result.items)
            self.metrics.last_cycle_processed_rows = len(processed)
            self.metrics.last_cycle_written_rows = write_summary.normalized_rows_inserted
            self.metrics.last_cycle_skipped_existing = write_summary.skipped_existing
            self.metrics.last_cycle_wall_seconds = time.perf_counter() - started
            self._log_event(
                "poll_completed",
                poll_id=poll_id,
                status=self.metrics.last_cycle_status,
                coverage_mode=coverage_mode,
                start_utc=start_utc,
                end_utc=end_utc,
                provider_rows=len(fetch_result.items),
                processed_rows=len(processed),
                failed_rows=failed,
                pages=fetch_result.pages,
                saturated=fetch_result.saturated,
                normalized_rows_inserted=write_summary.normalized_rows_inserted,
                ticker_rows_inserted=write_summary.ticker_rows_inserted,
                skipped_existing=write_summary.skipped_existing,
                skipped_reason="canonical_news_id_exists" if write_summary.skipped_existing else "",
                skipped_existing_ids_sample=skipped_ids[:skip_sample_size],
                skipped_existing_ids_sample_count=min(len(skipped_ids), skip_sample_size),
                skipped_existing_ids_total=len(skipped_ids),
                input_duplicate_ids_sample=duplicate_ids[:skip_sample_size],
                input_duplicate_ids_total=len(duplicate_ids),
                warnings=write_summary.warnings,
                wall_seconds=self.metrics.last_cycle_wall_seconds,
            )
            if failed == 0 and not fetch_result.saturated:
                await self._record_successful_coverage(
                    start_utc,
                    end_utc,
                    coverage_mode=coverage_mode,
                    provider_rows=len(fetch_result.items),
                    processed_rows=len(processed),
                    written_rows=write_summary.normalized_rows_inserted,
                    skipped_existing=write_summary.skipped_existing,
                )
            return {
                "status": self.metrics.last_cycle_status,
                "start_utc": start_utc.isoformat().replace("+00:00", "Z"),
                "end_utc": end_utc.isoformat().replace("+00:00", "Z"),
                "provider_rows": len(fetch_result.items),
                "processed_rows": len(processed),
                "failed_rows": failed,
                "pages": fetch_result.pages,
                "saturated": fetch_result.saturated,
                "write_summary": asdict(write_summary),
                "wall_seconds": self.metrics.last_cycle_wall_seconds,
            }
        except Exception as exc:  # noqa: BLE001
            self.metrics.poll_failures += 1
            self.metrics.last_error = repr(exc)
            self.metrics.last_cycle_status = "failed"
            self.metrics.last_cycle_wall_seconds = time.perf_counter() - started
            self.logger.exception(
                "poll_failed",
                exc,
                poll_id=poll_id,
                coverage_mode=coverage_mode,
                start_utc=start_utc,
                end_utc=end_utc,
                wall_seconds=self.metrics.last_cycle_wall_seconds,
            )
            return {"status": "failed", "exception": repr(exc), "wall_seconds": time.perf_counter() - started}

    def current_poll_seconds(self) -> float:
        now_et = datetime.now(EASTERN)
        minutes = now_et.hour * 60 + now_et.minute
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return self.config.premarket_poll_seconds
        if 9 * 60 + 30 <= minutes < 16 * 60:
            return self.config.market_poll_seconds
        if 16 * 60 <= minutes < 20 * 60:
            return self.config.afterhours_poll_seconds
        return self.config.closed_poll_seconds

    def snapshot_metrics(self) -> dict[str, Any]:
        return asdict(self.metrics)

    async def _publish_processed(self, processed: list[ProcessedNewsItem], *, poll_id: str, coverage_mode: str) -> Any:
        row_count = len(processed)
        task = asyncio.create_task(
            asyncio.to_thread(self._write_processed, processed),
            name=f"benzinga-news-publish-{poll_id}",
        )
        self._publish_tasks.add(task)
        self._publish_task_rows[task] = row_count
        self.metrics.publish_status = "running"
        self.metrics.publish_active_jobs = len(self._publish_tasks)
        self.metrics.publish_pending_rows += row_count
        self.metrics.publish_last_message = f"Publishing {row_count:,} processed row(s) for {coverage_mode} poll {poll_id}."
        self._set_phase(f"{coverage_mode}_write", self.metrics.publish_last_message)
        self._log_event(
            "publish_started",
            poll_id=poll_id,
            coverage_mode=coverage_mode,
            processed_rows=row_count,
            active_jobs=len(self._publish_tasks),
        )
        try:
            summary = await asyncio.shield(task)
        except Exception as exc:
            self.metrics.publish_failed_jobs += 1
            self.metrics.publish_status = "failed"
            self.metrics.publish_last_message = f"Publish failed for {poll_id}: {exc!r}"
            self.logger.exception(
                "publish_failed",
                exc,
                poll_id=poll_id,
                coverage_mode=coverage_mode,
                processed_rows=row_count,
            )
            raise
        finally:
            if task.done():
                self._publish_tasks.discard(task)
                rows = self._publish_task_rows.pop(task, row_count)
                self.metrics.publish_pending_rows = max(0, self.metrics.publish_pending_rows - rows)
                self.metrics.publish_active_jobs = len(self._publish_tasks)
                if self._publish_tasks:
                    self.metrics.publish_status = "running"
                elif self.metrics.publish_status != "failed":
                    self.metrics.publish_status = "idle"
        self.metrics.publish_completed_jobs += 1
        self.metrics.publish_last_message = (
            f"Publish completed for {poll_id}: "
            f"inserted={getattr(summary, 'normalized_rows_inserted', 0):,}, "
            f"skipped={getattr(summary, 'skipped_existing', 0):,}."
        )
        self._log_event(
            "publish_completed",
            poll_id=poll_id,
            coverage_mode=coverage_mode,
            processed_rows=row_count,
            normalized_rows_inserted=getattr(summary, "normalized_rows_inserted", 0),
            ticker_rows_inserted=getattr(summary, "ticker_rows_inserted", 0),
            skipped_existing=getattr(summary, "skipped_existing", 0),
            active_jobs=len(self._publish_tasks),
        )
        return summary

    async def _drain_publish_tasks(self, reason: str) -> None:
        while self._publish_tasks:
            active = len(self._publish_tasks)
            pending_rows = self.metrics.publish_pending_rows
            message = (
                f"Termination requested; waiting for {active:,} database publish job(s) "
                f"with {pending_rows:,} row(s) still pending."
            )
            self.metrics.publish_status = "draining"
            self.metrics.publish_active_jobs = active
            self.metrics.publish_last_message = message
            self._set_phase("shutdown_waiting_for_publish", message)
            self._log_event("shutdown_waiting_for_publish", reason=reason, active_jobs=active, pending_rows=pending_rows)
            self._print_status(message)
            done, _pending = await asyncio.wait(self._publish_tasks, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                self._publish_tasks.discard(task)
                rows = self._publish_task_rows.pop(task, 0)
                self.metrics.publish_pending_rows = max(0, self.metrics.publish_pending_rows - rows)
                if task.cancelled():
                    self.metrics.publish_failed_jobs += 1
                    self._log_event("publish_task_cancelled_during_shutdown", reason=reason)
                    continue
                exc = task.exception()
                if exc is not None:
                    self.metrics.publish_failed_jobs += 1
                    self.logger.exception("publish_task_failed_during_shutdown", exc, reason=reason)
            self.metrics.publish_active_jobs = len(self._publish_tasks)
        if self.metrics.publish_status == "draining":
            self.metrics.publish_status = "idle"
            self.metrics.publish_pending_rows = 0
            message = "All pending database publish jobs finished; continuing graceful shutdown."
            self.metrics.publish_last_message = message
            self._set_phase("shutdown_publish_drained", message)
            self._log_event("shutdown_publish_drained", reason=reason)

    async def _wait_for_service_tasks_to_quiesce(self) -> None:
        tasks = [task for task in [self._poll_task, self._gap_task] if task is not None and not task.done()]
        if not tasks:
            return
        timeout = max(1.0, self.config.graceful_shutdown_seconds)
        message = (
            f"Termination requested; waiting up to {timeout:.0f}s for active polling/gap work "
            "to reach a safe stop point before cancellation."
        )
        self._set_phase("shutdown_waiting_for_workers", message)
        self._log_event("shutdown_waiting_for_workers", task_count=len(tasks), timeout_seconds=timeout)
        self._print_status(message)
        done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.ALL_COMPLETED)
        if pending:
            warning = (
                f"Graceful worker wait timed out with {len(pending):,} task(s) still active; "
                "pending database publish tasks will still be drained before shutdown completes."
            )
            self.metrics.last_error = warning
            self._set_phase("shutdown_worker_wait_timeout", warning)
            self._log_event("shutdown_worker_wait_timeout", done=len(done), pending=len(pending), timeout_seconds=timeout)
            self._print_status(warning)

    def _write_processed(self, processed: list[ProcessedNewsItem]) -> Any:
        summaries = []
        for index in range(0, len(processed), max(1, self.config.write_batch_size)):
            chunk = processed[index : index + self.config.write_batch_size]
            summaries.append(
                self.pipeline.write_many(
                    chunk,
                    target=self.target,
                    execute=self.config.execute,
                    skip_existing=True,
                )
            )
        if not summaries:
            return self.pipeline.write_many([], target=self.target, execute=self.config.execute, skip_existing=True)
        if len(summaries) == 1:
            return summaries[0]
        first = summaries[0]
        return type(first)(
            status="written" if self.config.execute else "dry_run",
            execute=self.config.execute,
            input_results=sum(item.input_results for item in summaries),
            normalized_rows_inserted=sum(item.normalized_rows_inserted for item in summaries),
            ticker_rows_inserted=sum(item.ticker_rows_inserted for item in summaries),
            skipped_existing=sum(item.skipped_existing for item in summaries),
            warnings=sorted({warning for item in summaries for warning in item.warnings}),
            skipped_existing_ids=sorted({skip_id for item in summaries for skip_id in getattr(item, "skipped_existing_ids", [])}),
            input_duplicate_ids=sorted({duplicate_id for item in summaries for duplicate_id in getattr(item, "input_duplicate_ids", [])}),
        )

    async def _prepare_coverage_manifest(self) -> None:
        if not self.config.execute:
            return
        self._print_status(f"coverage_manifest_bootstrap=started chunk_seconds={self.config.coverage_discovery_chunk_seconds}")
        summary = await asyncio.to_thread(self._ensure_and_bootstrap_coverage_manifest)
        if summary.executed:
            message = (
                "Coverage manifest bootstrap completed: "
                f"chunk={summary.chunk_seconds}s non_empty_buckets={summary.non_empty_buckets:,} "
                f"covered_intervals={summary.covered_intervals:,} "
                f"discovered_gap_intervals={summary.discovered_gap_intervals:,} "
                f"unique_gap_days={summary.discovered_gap_unique_days:,} "
                f"empty_gap_hours={summary.discovered_gap_seconds / 3600:.1f}."
            )
            self.metrics.gap_status = "coverage_bootstrapped"
            self.metrics.gap_message = message
            self._print_status(message)
            self._log_event("coverage_bootstrap_completed", summary=asdict(summary), message=message)
        else:
            message = f"Coverage manifest bootstrap skipped: status={summary.status} chunk={summary.chunk_seconds}s."
            self.metrics.gap_message = message
            self._print_status(message)
            self._log_event("coverage_bootstrap_skipped", summary=asdict(summary), message=message)

    def _ensure_and_bootstrap_coverage_manifest(self) -> CoverageBootstrapSummary:
        client = self._coverage_client()
        config = self._coverage_config()
        ensure_coverage_manifest_table(client, config)
        trusted_start = parse_optional_utc(self.config.bootstrap_trusted_coverage_start_utc)
        trusted_end = parse_optional_utc(self.config.bootstrap_trusted_coverage_end_utc)
        verify_after = parse_optional_utc(self.config.bootstrap_verify_gaps_after_utc)
        gap_probe = self._probe_gap_is_empty if self.config.bootstrap_probe_recent_gaps else None
        self._bootstrap_probe_count = 0
        self._bootstrap_probe_empty = 0
        self._bootstrap_probe_positive = 0
        self.metrics.bootstrap_probe_total = 0
        self.metrics.bootstrap_probe_completed = 0
        self.metrics.bootstrap_probe_empty = 0
        self.metrics.bootstrap_probe_positive = 0
        summary = bootstrap_coverage_from_normalized_table(
            client,
            config,
            chunk_seconds=self.config.coverage_discovery_chunk_seconds,
            force_rebuild=self.config.rebuild_coverage_manifest,
            trusted_coverage_start_utc=trusted_start,
            trusted_coverage_end_utc=trusted_end,
            verify_gaps_after_utc=verify_after,
            gap_probe=gap_probe,
            gap_probe_plan=self._record_bootstrap_probe_plan if gap_probe is not None else None,
        )
        if self.config.coverage_compact_on_startup:
            compact_summary = compact_coverage_manifest(
                client,
                config,
                tolerance_seconds=max(0, self.config.coverage_compact_tolerance_seconds),
                run_id=self._run_id,
            )
            self._log_event("coverage_manifest_compacted", summary=compact_summary)
        return summary

    def _record_bootstrap_probe_plan(self, gaps: list[CoverageGap]) -> None:
        total = len(gaps)
        self._bootstrap_probe_count = 0
        self._bootstrap_probe_empty = 0
        self._bootstrap_probe_positive = 0
        self.metrics.bootstrap_probe_total = total
        self.metrics.bootstrap_probe_completed = 0
        self.metrics.bootstrap_probe_empty = 0
        self.metrics.bootstrap_probe_positive = 0
        if total <= 0:
            return
        first_gap = gaps[0]
        last_gap = gaps[-1]
        message = (
            f"Coverage bootstrap will probe {total:,} recent gap(s): "
            f"{first_gap.start_utc.isoformat()} -> {last_gap.end_utc.isoformat()}."
        )
        self.metrics.current_phase = "coverage_gap_probe_plan"
        self.metrics.current_phase_message = message
        self.metrics.current_phase_started_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._log_event(
            "coverage_gap_provider_probe_plan",
            gap_count=total,
            first_start_utc=first_gap.start_utc,
            last_end_utc=last_gap.end_utc,
        )
        self._print_status(message)

    def _probe_gap_is_empty(self, gap: CoverageGap) -> bool:
        self._bootstrap_probe_count += 1
        probe_index = self._bootstrap_probe_count
        started = time.perf_counter()
        total = self.metrics.bootstrap_probe_total
        self.metrics.current_phase = "coverage_gap_probe"
        progress_label = f"{probe_index:,}/{total:,}" if total else f"{probe_index:,}"
        self.metrics.current_phase_message = f"Probing recent coverage gap {progress_label}: {gap.start_utc.isoformat()} -> {gap.end_utc.isoformat()}."
        self.metrics.current_phase_started_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._log_event(
            "coverage_gap_provider_probe_started",
            probe_index=probe_index,
            probe_total=total,
            start_utc=gap.start_utc,
            end_utc=gap.end_utc,
        )
        if self._should_print_bootstrap_probe_progress(probe_index):
            self._print_status(self.metrics.current_phase_message)
        try:
            result = self.provider.probe_window(gap.start_utc, gap.end_utc)
        except Exception as exc:
            self.logger.exception(
                "coverage_gap_provider_probe_failed",
                exc,
                probe_index=probe_index,
                start_utc=gap.start_utc,
                end_utc=gap.end_utc,
                wall_seconds=time.perf_counter() - started,
            )
            raise
        is_empty = not result.has_news
        if is_empty:
            self._bootstrap_probe_empty += 1
        else:
            self._bootstrap_probe_positive += 1
        self.metrics.bootstrap_probe_completed = probe_index
        self.metrics.bootstrap_probe_empty = self._bootstrap_probe_empty
        self.metrics.bootstrap_probe_positive = self._bootstrap_probe_positive
        message = (
            f"Coverage probe {progress_label} complete: decision={'covered_empty' if is_empty else 'gap_requires_fill'} "
            f"empty={self._bootstrap_probe_empty} positive={self._bootstrap_probe_positive}."
        )
        self.metrics.current_phase_message = message
        self._log_event(
            "coverage_gap_provider_probe",
            probe_index=probe_index,
            probe_total=total,
            start_utc=gap.start_utc,
            end_utc=gap.end_utc,
            has_news=result.has_news,
            rows_seen=result.rows_seen,
            pages=result.pages,
            decision="covered_empty" if is_empty else "gap_requires_fill",
            wall_seconds=time.perf_counter() - started,
            empty_count=self._bootstrap_probe_empty,
            positive_count=self._bootstrap_probe_positive,
        )
        if not is_empty or self._should_print_bootstrap_probe_progress(probe_index):
            self._print_status(message)
        return is_empty

    def _should_print_bootstrap_probe_progress(self, probe_index: int) -> bool:
        interval = max(1, self.config.bootstrap_probe_progress_interval)
        return probe_index <= 5 or probe_index % interval == 0

    def _load_coverage_intervals(self) -> list[CoverageInterval]:
        client = self._coverage_client()
        return load_coverage_intervals(client, self._coverage_config())

    async def _open_live_coverage(self) -> None:
        if not self.config.execute:
            return
        now = datetime.now(UTC)
        self._live_coverage_start = now
        self._live_coverage_end = now
        await asyncio.to_thread(self._write_live_coverage_snapshot, status="running", closed_at=None)

    async def _close_live_coverage(self) -> None:
        if not self.config.execute or self._live_coverage_start is None or self._live_coverage_end is None:
            return
        await asyncio.to_thread(self._write_live_coverage_snapshot, status="completed", closed_at=datetime.now(UTC))

    async def _record_successful_coverage(
        self,
        start_utc: datetime,
        end_utc: datetime,
        *,
        coverage_mode: str,
        provider_rows: int,
        processed_rows: int,
        written_rows: int,
        skipped_existing: int,
    ) -> None:
        if not self.config.execute:
            return
        if coverage_mode == "gap_fill":
            now = datetime.now(UTC)
            self._gap_coverage_counter += 1
            await asyncio.to_thread(
                self._write_completed_gap_coverage,
                start_utc,
                end_utc,
                provider_rows,
                processed_rows,
                written_rows,
                skipped_existing,
                coverage_id=f"{self._run_id}_gap_{self._gap_coverage_counter:06d}",
                started_at_utc=now,
                status="completed",
                closed_at=now,
            )
            return
        if coverage_mode != "live":
            return
        tolerance = timedelta(seconds=max(0, self.config.poll_overlap_seconds))
        if self._live_coverage_start is None or self._live_coverage_end is None:
            self._live_coverage_start = start_utc
            self._live_coverage_end = end_utc
        elif start_utc > self._live_coverage_end + tolerance:
            await asyncio.to_thread(self._write_live_coverage_snapshot, status="completed", closed_at=datetime.now(UTC))
            self._live_coverage_id = f"{self._run_id}_live_{uuid_suffix()}"
            self._live_coverage_started_at = datetime.now(UTC)
            self._live_coverage_start = start_utc
            self._live_coverage_end = end_utc
            self._live_coverage_poll_runs = 0
            self._live_coverage_provider_rows = 0
            self._live_coverage_processed_rows = 0
            self._live_coverage_written_rows = 0
            self._live_coverage_failed_rows = 0
            self._live_coverage_skipped_existing = 0
        else:
            self._live_coverage_start = min(self._live_coverage_start, start_utc)
            self._live_coverage_end = max(self._live_coverage_end, end_utc)
        self._live_coverage_poll_runs += 1
        self._live_coverage_provider_rows += provider_rows
        self._live_coverage_processed_rows += processed_rows
        self._live_coverage_written_rows += written_rows
        self._live_coverage_skipped_existing += skipped_existing
        await asyncio.to_thread(self._write_live_coverage_snapshot, status="running", closed_at=None)

    def _write_live_coverage_snapshot(self, *, status: str, closed_at: datetime | None) -> None:
        if self._live_coverage_start is None or self._live_coverage_end is None:
            return
        snapshot = CoverageSnapshot(
            coverage_id=self._live_coverage_id,
            run_id=self._run_id,
            source="live_gateway",
            status=status,
            coverage_start_utc=self._live_coverage_start,
            coverage_end_utc=self._live_coverage_end,
            started_at_utc=self._live_coverage_started_at,
            updated_at_utc=datetime.now(UTC),
            closed_at_utc=closed_at,
            poll_runs=self._live_coverage_poll_runs,
            provider_rows=self._live_coverage_provider_rows,
            processed_rows=self._live_coverage_processed_rows,
            written_rows=self._live_coverage_written_rows,
            failed_rows=self._live_coverage_failed_rows,
            skipped_existing=self._live_coverage_skipped_existing,
            last_error=self.metrics.last_error,
            metadata={"mode": "live", "lookback_minutes": self.config.lookback_minutes},
        )
        insert_coverage_snapshot(self._coverage_client(), self._coverage_config(), snapshot)
        self._log_event(
            "coverage_live_snapshot_written",
            coverage_id=snapshot.coverage_id,
            status=snapshot.status,
            start_utc=snapshot.coverage_start_utc,
            end_utc=snapshot.coverage_end_utc,
            poll_runs=snapshot.poll_runs,
            provider_rows=snapshot.provider_rows,
            processed_rows=snapshot.processed_rows,
            written_rows=snapshot.written_rows,
            skipped_existing=snapshot.skipped_existing,
        )

    def _write_completed_gap_coverage(
        self,
        start_utc: datetime,
        end_utc: datetime,
        provider_rows: int,
        processed_rows: int,
        written_rows: int,
        skipped_existing: int,
        *,
        coverage_id: str,
        started_at_utc: datetime,
        status: str,
        closed_at: datetime | None,
        chunk_count: int = 1,
        pages: int = 0,
    ) -> None:
        if not self.config.execute:
            return
        now = datetime.now(UTC)
        snapshot = CoverageSnapshot(
            coverage_id=coverage_id,
            run_id=self._run_id,
            source="gateway_gap_fill",
            status=status,
            coverage_start_utc=start_utc,
            coverage_end_utc=end_utc,
            started_at_utc=started_at_utc,
            updated_at_utc=now,
            closed_at_utc=closed_at,
            poll_runs=chunk_count,
            provider_rows=provider_rows,
            processed_rows=processed_rows,
            written_rows=written_rows,
            skipped_existing=skipped_existing,
            metadata={
                "mode": "startup_gap_fill",
                "chunk_minutes": self.config.gap_fill_chunk_minutes,
                "chunk_count": chunk_count,
                "pages": pages,
                "coverage_compaction": "contiguous_successful_chunks",
            },
        )
        insert_coverage_snapshot(self._coverage_client(), self._coverage_config(), snapshot)
        self._log_event(
            "coverage_gap_snapshot_written",
            coverage_id=snapshot.coverage_id,
            status=snapshot.status,
            start_utc=snapshot.coverage_start_utc,
            end_utc=snapshot.coverage_end_utc,
            poll_runs=snapshot.poll_runs,
            provider_rows=snapshot.provider_rows,
            processed_rows=snapshot.processed_rows,
            written_rows=snapshot.written_rows,
            skipped_existing=snapshot.skipped_existing,
            metadata=snapshot.metadata,
        )

    def _write_gap_coverage_run(self, coverage_run: GapCoverageRun, status: str, closed_at: datetime | None) -> None:
        self._write_completed_gap_coverage(
            coverage_run.start_utc,
            coverage_run.end_utc,
            coverage_run.provider_rows,
            coverage_run.processed_rows,
            coverage_run.written_rows,
            coverage_run.skipped_existing,
            coverage_id=coverage_run.coverage_id,
            started_at_utc=coverage_run.started_at_utc,
            status=status,
            closed_at=closed_at,
            chunk_count=coverage_run.chunk_count,
            pages=coverage_run.pages,
        )

    def _extend_gap_coverage_run(
        self,
        coverage_run: GapCoverageRun | None,
        start_utc: datetime,
        end_utc: datetime,
        result: dict[str, Any],
    ) -> GapCoverageRun:
        if coverage_run is None:
            self._gap_coverage_counter += 1
            coverage_run = GapCoverageRun(
                coverage_id=f"{self._run_id}_gap_{self._gap_coverage_counter:06d}",
                started_at_utc=datetime.now(UTC),
                start_utc=start_utc,
                end_utc=end_utc,
            )
        else:
            coverage_run.end_utc = end_utc
        coverage_run.chunk_count += 1
        coverage_run.provider_rows += int(result.get("provider_rows") or 0)
        coverage_run.processed_rows += int(result.get("processed_rows") or 0)
        coverage_run.written_rows += int((result.get("write_summary") or {}).get("normalized_rows_inserted") or 0)
        coverage_run.skipped_existing += int((result.get("write_summary") or {}).get("skipped_existing") or 0)
        coverage_run.pages += int(result.get("pages") or 0)
        return coverage_run

    def _coverage_client(self) -> ClickHouseHttpClient:
        return ClickHouseHttpClient(self.target.url, self.target.user, self.target.password)

    def _coverage_config(self) -> CoverageManifestConfig:
        return CoverageManifestConfig(
            database=self.target.database,
            coverage_table=self.target.coverage_table,
            normalized_table=self.target.normalized_table,
            storage_policy=__import__("os").environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )

    def _log_event(self, event: str, **payload: Any) -> None:
        self.logger.event(event, **payload)

    def _print_status(self, message: str) -> None:
        if self.config.terminal_rich_enabled:
            return
        print(message, flush=True)


def save_raw_payload(raw_root: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    try:
        published = parse_provider_datetime(str(payload.get("published") or ""))
    except Exception:
        published = datetime.now(UTC)
    raw_path = artifact_path_for_payload(raw_root.parent, payload, published)
    raw_hash = write_raw_payload(raw_path, payload)
    return raw_path, raw_hash


def parse_optional_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return parse_clickhouse_datetime(text)


def count_unique_utc_days_for_gaps(gaps: list[CoverageGap]) -> int:
    days: set[str] = set()
    for gap in gaps:
        if gap.end_utc <= gap.start_utc:
            continue
        cursor = gap.start_utc.astimezone(UTC).date()
        end_day = (gap.end_utc - timedelta(microseconds=1)).astimezone(UTC).date()
        while cursor <= end_day:
            days.add(cursor.isoformat())
            cursor += timedelta(days=1)
    return len(days)


def build_gap_fill_chunks(start_utc: datetime, end_utc: datetime, chunk_minutes: int) -> list[GapFillChunk]:
    chunks: list[GapFillChunk] = []
    current = start_utc
    index = 0
    step = timedelta(minutes=max(1, chunk_minutes))
    while current < end_utc:
        chunk_end = min(current + step, end_utc)
        chunks.append(GapFillChunk(index=index, start_utc=current, end_utc=chunk_end))
        current = chunk_end
        index += 1
    return chunks


def historical_gap_command(start_utc: datetime, end_utc: datetime, config: NewsGatewayConfig) -> str:
    raw_root = config.raw_root_win if config.is_workstation else WORKSTATION_DATA_ROOT_WIN / "news-benzinga" / "raw"
    return (
        "python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill "
        f"--start-utc {start_utc.isoformat().replace('+00:00', 'Z')} "
        f"--end-utc {end_utc.isoformat().replace('+00:00', 'Z')} "
        f"--raw-root-win {quote_arg(str(raw_root))} "
        f"--bucket-minutes {max(1, config.gap_fill_chunk_minutes)} --workers 4 --batch-size 1000 --progress-interval 10 --execute"
    )


def write_manual_gap_fill_plan(intervals: list[GapFillInterval], config: NewsGatewayConfig) -> ManualGapFillPlan:
    if not intervals:
        raise ValueError("manual gap fill plan requires at least one interval")
    now = datetime.now(UTC)
    run_id = "news_gateway_gap_" + now.strftime("%Y%m%d_%H%M%S")
    manifest_run_root = config.manual_gap_manifest_root_win / run_id
    script_run_root_display = config.manual_gap_script_root_win / run_id
    script_run_root_write = writable_workstation_code_path(script_run_root_display, config.workstation_code_root_win)
    manifest_run_root.mkdir(parents=True, exist_ok=True)
    script_run_root_write.mkdir(parents=True, exist_ok=True)
    script_path = script_run_root_write / f"{run_id}_run_all.ps1"
    manifest_path = manifest_run_root / f"{run_id}_manifest.json"
    raw_root = WORKSTATION_DATA_ROOT_WIN / "news-benzinga" / "raw"
    output_root = WORKSTATION_DATA_ROOT_WIN / "prepared" / "benzinga_news_provider_gap_fill"
    jobs = []
    for index, interval in enumerate(intervals, start=1):
        start_text = interval.start_utc.isoformat().replace("+00:00", "Z")
        end_text = interval.end_utc.isoformat().replace("+00:00", "Z")
        child_name = f"{run_id}_job_{index:03d}_{filename_time(start_text)}_{filename_time(end_text)}.ps1"
        jobs.append(
            {
                "index": index,
                "start_utc": start_text,
                "end_utc": end_text,
                "script_name": child_name,
                "script_path": str(workstation_code_display_path(script_run_root_display / child_name)),
            }
        )
    manifest = {
        "run_id": run_id,
        "created_at_utc": now.isoformat().replace("+00:00", "Z"),
        "created_by": "services.news_gateway",
        "reason": "manual_required_large_gap",
        "workstation_code_root_win": str(config.workstation_code_root_win),
        "workstation_conda_env": config.workstation_conda_env,
        "raw_root_win": str(raw_root),
        "output_root_win": str(output_root),
        "manifest_root_win": str(workstation_path_for_share(manifest_run_root)),
        "script_root_win": str(workstation_code_display_path(script_run_root_display)),
        "interval_count": len(jobs),
        "intervals": jobs,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    for job in jobs:
        (script_run_root_write / str(job["script_name"])).write_text(manual_gap_fill_job_script(job, manifest, config), encoding="utf-8")
    script_path.write_text(manual_gap_fill_master_script(manifest, config), encoding="utf-8")
    return ManualGapFillPlan(
        script_path=script_path,
        manifest_path=manifest_path,
        workstation_script_path=workstation_code_display_path(script_run_root_display / script_path.name),
        workstation_manifest_path=workstation_path_for_share(manifest_path),
        intervals=intervals,
    )


def manual_gap_fill_master_script(manifest: dict[str, Any], config: NewsGatewayConfig) -> str:
    jobs = manifest["intervals"]
    job_rows = "\n".join(
        "  [pscustomobject]@{ Index = %d; StartUtc = '%s'; EndUtc = '%s'; ScriptName = '%s' }"
        % (
            int(job["index"]),
            ps_single(str(job["start_utc"])),
            ps_single(str(job["end_utc"])),
            ps_single(str(job["script_name"])),
        )
        for job in jobs
    )
    code_root = ps_single(str(config.workstation_code_root_win))
    conda_env = ps_single(config.workstation_conda_env)
    manifest_path = ps_single(str(Path(str(manifest["manifest_root_win"])) / str(manifest["run_id"] + "_manifest.json")))
    return f"""# Generated by services.news_gateway on {manifest['created_at_utc']}.
# Master script. Run this script in PowerShell on the workstation.
$ErrorActionPreference = "Stop"
$CodeRoot = '{code_root}'
$CondaEnv = '{conda_env}'
$ManifestPath = '{manifest_path}'

if (-not (Test-Path $CodeRoot)) {{
  throw "Workstation code root was not found: $CodeRoot"
}}
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {{
  throw "conda was not found. Open an Anaconda/Miniconda PowerShell prompt or activate conda first."
}}

$Jobs = @(
{job_rows}
)

Set-Location $CodeRoot
Write-Host "Benzinga news manual gap fill"
Write-Host "manifest=$ManifestPath"
Write-Host "jobs=$($Jobs.Count) code_root=$CodeRoot conda_env=$CondaEnv"

foreach ($Job in $Jobs) {{
  Write-Host ("=" * 96)
  Write-Host "gap_job=$($Job.Index)/$($Jobs.Count) start=$($Job.StartUtc) end=$($Job.EndUtc)"
  $ChildScriptPath = Join-Path $PSScriptRoot $Job.ScriptName
  & $ChildScriptPath
  if ($LASTEXITCODE -ne 0) {{
    throw "gap job $($Job.Index) failed with exit code $LASTEXITCODE"
  }}
}}

Write-Host ("=" * 96)
Write-Host "Benzinga news manual gap fill completed."
"""


def manual_gap_fill_job_script(job: dict[str, Any], manifest: dict[str, Any], config: NewsGatewayConfig) -> str:
    code_root = ps_single(str(config.workstation_code_root_win))
    conda_env = ps_single(config.workstation_conda_env)
    raw_root = ps_single(str(manifest["raw_root_win"]))
    output_root = ps_single(str(manifest["output_root_win"]))
    start_utc = ps_single(str(job["start_utc"]))
    end_utc = ps_single(str(job["end_utc"]))
    index = int(job["index"])
    count = int(manifest["interval_count"])
    return f"""# Generated by services.news_gateway on {manifest['created_at_utc']}.
# Gap-fill child script {index} of {count}. Run the *_run_all.ps1 master script unless this interval is the only one you need.
$ErrorActionPreference = "Stop"
$CodeRoot = '{code_root}'
$CondaEnv = '{conda_env}'
$RawRoot = '{raw_root}'
$OutputRoot = '{output_root}'
$StartUtc = '{start_utc}'
$EndUtc = '{end_utc}'

if (-not (Test-Path $CodeRoot)) {{
  throw "Workstation code root was not found: $CodeRoot"
}}
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {{
  throw "conda was not found. Open an Anaconda/Miniconda PowerShell prompt or activate conda first."
}}

Set-Location $CodeRoot
conda run --no-capture-output -n $CondaEnv python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill `
  --start-utc $StartUtc `
  --end-utc $EndUtc `
  --raw-root-win $RawRoot `
  --output-root-win $OutputRoot `
  --bucket-minutes {max(1, config.gap_fill_chunk_minutes)} `
  --workers 4 `
  --batch-size 1000 `
  --progress-interval 10 `
  --execute
if ($LASTEXITCODE -ne 0) {{
  throw "gap child job {index} failed with exit code $LASTEXITCODE"
}}
"""


def filename_time(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace("+", "")
        .replace("Z", "Z")
    )


def workstation_path_for_share(path: Path) -> Path:
    text = str(path)
    share = str(WORKSTATION_SHARE_DATA_ROOT_WIN)
    if text.lower().startswith(share.lower()):
        relative = text[len(share) :].lstrip("\\/")
        return WORKSTATION_DATA_ROOT_WIN / relative
    return path


def writable_workstation_code_path(path: Path, workstation_code_root: Path) -> Path:
    if str(path).lower().startswith(str(workstation_code_root).lower()):
        relative = str(path)[len(str(workstation_code_root)) :].lstrip("\\/")
        if workstation_code_root.exists():
            return workstation_code_root / relative
        if str(workstation_code_root).lower() == str(WORKSTATION_CODE_ROOT_WIN).lower():
            return WORKSTATION_SHARE_CODE_ROOT_WIN / relative
    if str(path).lower().startswith(str(WORKSTATION_CODE_ROOT_WIN).lower()):
        relative = str(path)[len(str(WORKSTATION_CODE_ROOT_WIN)) :].lstrip("\\/")
        return WORKSTATION_SHARE_CODE_ROOT_WIN / relative
    return path


def workstation_code_display_path(path: Path) -> Path:
    text = str(path)
    share = str(WORKSTATION_SHARE_CODE_ROOT_WIN)
    if text.lower().startswith(share.lower()):
        relative = text[len(share) :].lstrip("\\/")
        return WORKSTATION_CODE_ROOT_WIN / relative
    return path


def ps_single(value: str) -> str:
    return value.replace("'", "''")


def quote_arg(value: str) -> str:
    return f'"{value}"' if " " in value or "\\" in value else value


def massive_api_key() -> str:
    value = __import__("os").environ.get("MASSIVE_API_KEY", "").strip()
    if not value:
        raise RuntimeError("MASSIVE_API_KEY is required")
    return value


def uuid_suffix() -> str:
    return uuid.uuid4().hex[:10]
