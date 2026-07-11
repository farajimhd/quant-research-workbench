from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import SecClickHouseWriter, SecWriteResult, qi, sql_string
from pipelines.sec.edgar.sec_pipeline.coverage import (
    KIND_LIVE_FEED,
    SecCoverageConfig,
    SecGap,
    bootstrap_from_existing_tables,
    ensure_coverage_table,
    insert_coverage,
    new_coverage_id,
    plan_coverage_gaps,
)
from pipelines.sec.edgar.sec_pipeline.feed import SecCurrentFeedClient, SecFeedItem
from pipelines.sec.edgar.sec_pipeline.historical_fill import (
    build_integrity_audit_plan,
    build_historical_fill_plan,
    run_plan_script,
    write_multi_plan_script,
)
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient
from pipelines.sec.edgar.sec_pipeline.live_pipeline import SecLiveFilingPipeline
from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter
from research.mlops.clickhouse import ClickHouseHttpClient
from services.news_gateway.run_logger import AsyncRunLogger
from services.sec_gateway.config import SecGatewayConfig, WORKSTATION_SHARE_CODE_ROOT_WIN
from services.sec_gateway.preflight import PreflightError, PreflightReport, run_preflight
from services.gateway_policy import backfill_auto_run_allowed, maintenance_window_message
from services.gateway_core.market_calendar import MarketHoursSnapshot, MassiveMarketHoursClient


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class SecGatewayMetrics:
    started_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    current_phase: str = "starting"
    current_phase_message: str = "Starting SEC gateway."
    preflight_status: str = "not_started"
    preflight_checked_at_utc: str = ""
    preflight_checks: list[dict[str, Any]] = field(default_factory=list)
    poll_runs: int = 0
    poll_failures: int = 0
    feed_items: int = 0
    processed_filings: int = 0
    written_filings: int = 0
    skipped_existing: int = 0
    failed_filings: int = 0
    last_poll_at_utc: str = ""
    last_error: str = ""
    last_error_status: str = ""
    last_error_seen_at_utc: str = ""
    last_error_resolved_at_utc: str = ""
    last_accession: str = ""
    last_form_type: str = ""
    gap_status: str = "not_started"
    gap_message: str = ""
    gap_count: int = 0
    manual_gap_fill_script_win: str = ""
    manual_gap_fill_command: str = ""
    audit_status: str = "not_started"
    audit_message: str = ""
    market_status: str = ""
    market_status_source: str = "local_clock"
    market_status_server_time: str = ""
    market_status_updated_at_utc: str = ""
    market_status_error: str = ""
    current_poll_seconds: float = 0.0
    sec_request_cooldown_remaining_seconds: float = 0.0
    sec_request_cooldown_reason: str = ""
    xbrl_concept_rows: int = 0
    xbrl_company_fact_rows: int = 0
    xbrl_frame_rows: int = 0
    xbrl_frame_observation_rows: int = 0
    run_log_path: str = ""
    live_queue_size: int = 0
    live_queue_max_items: int = 0
    live_workers: int = 0
    live_active_workers: int = 0
    live_queued_filings: int = 0
    live_completed_filings: int = 0
    live_worker_failures: int = 0
    last_worker_message: str = ""
    coverage_interval_count: int = 0
    pending_shutdown_jobs: int = 0
    submissions_cache_entries: int = 0
    submissions_cache_limit: int = 0
    submissions_cache_max_age_seconds: int = 0
    xbrl_payload_cache_entries: int = 0
    xbrl_payload_cache_limit: int = 0
    xbrl_payload_cache_max_age_seconds: int = 0
    xbrl_missing_cik_cache_entries: int = 0
    xbrl_missing_cik_cache_limit: int = 0
    recent_metadata_rows: int = 0
    recent_metadata_retention_hours: float = 0.0


@dataclass(frozen=True, slots=True)
class SecLiveJob:
    item: SecFeedItem
    poll_id: str


@dataclass(frozen=True, slots=True)
class SecLiveOutcome:
    item: SecFeedItem
    poll_id: str
    result: SecWriteResult | None = None
    error: str = ""


@dataclass(slots=True)
class SecPollCoverageState:
    poll_id: str
    start_utc: datetime
    end_utc: datetime
    feed_items: int
    skipped_existing: int
    pending_jobs: int
    failed_jobs: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class SecGateway:
    def __init__(self, config: SecGatewayConfig) -> None:
        self.config = config
        self.metrics = SecGatewayMetrics()
        self._run_id = f"sec_gateway_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._terminal_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._live_queue: asyncio.Queue[SecLiveJob | None] = asyncio.Queue(maxsize=max(1, config.live_queue_max_items))
        self._inflight_accessions: set[str] = set()
        self._inflight_lock = asyncio.Lock()
        self._pending_poll_coverage: dict[str, SecPollCoverageState] = {}
        self._recent: list[dict[str, Any]] = []
        self._coverage_id = new_coverage_id("sec_live_feed")
        self._live_coverage_start_utc: datetime | None = None
        self._live_coverage_end_utc: datetime | None = None
        self._live_coverage_rows = 0
        self._live_coverage_errors = 0
        self._write_batches_since_audit = 0
        self.logger = AsyncRunLogger(
            root=config.pipeline.prepared_root_win / "sec_gateway" / "logs",
            run_id=self._run_id,
            enabled=config.run_log_enabled,
            queue_size=config.run_log_queue_size,
        )
        self.logger.path = self.logger.path.with_name("sec_gateway_events.jsonl")
        self.metrics.run_log_path = str(self.logger.path) if config.run_log_enabled else ""
        self.metrics.live_queue_max_items = max(1, config.live_queue_max_items)
        self.metrics.live_workers = max(1, config.live_workers)
        self._client = ClickHouseHttpClient(config.pipeline.clickhouse.url, config.pipeline.clickhouse.user, config.pipeline.clickhouse.password)
        self._coverage = SecCoverageConfig(
            database=config.pipeline.clickhouse.write_database,
            coverage_table=config.pipeline.clickhouse.coverage_table,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        self._writer = SecClickHouseWriter(self._client, database=config.pipeline.clickhouse.write_database)
        self._limiter = SecRateLimiter(config.pipeline.request_min_interval_seconds)
        self._http = SecHttpClient(
            user_agent=config.pipeline.sec_user_agent,
            rate_limiter=self._limiter,
            timeout_seconds=config.pipeline.request_timeout_seconds,
            transient_error_cooldown_seconds=config.pipeline.request_transient_error_cooldown_seconds,
            rate_limit_cooldown_seconds=config.pipeline.request_rate_limit_cooldown_seconds,
        )
        self._feed = SecCurrentFeedClient(feed_url=self.feed_url(), http=self._http)
        self._live_pipeline = SecLiveFilingPipeline(
            http=self._http,
            raw_root_win=config.pipeline.raw_live_root_win,
            submissions_cache_entries=config.submissions_cache_entries,
            submissions_cache_max_age_seconds=config.submissions_cache_max_age_seconds,
            xbrl_payload_cache_entries=config.xbrl_payload_cache_entries,
            xbrl_payload_cache_max_age_seconds=config.xbrl_payload_cache_max_age_seconds,
            xbrl_missing_cik_cache_entries=config.xbrl_missing_cik_cache_entries,
        )
        self._refresh_cache_metrics()
        self._market_status: MarketHoursSnapshot | None = None
        self._massive_api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
        self.market_status_provider = MassiveMarketHoursClient.from_env(
            service_prefix="SEC",
            api_key=self._massive_api_key,
            status_url=config.market_status_url,
            holidays_url=config.market_holidays_url,
            enabled=config.market_status_enabled,
            refresh_seconds=config.market_status_refresh_seconds,
        )

    async def start(self) -> None:
        await self.logger.start()
        self._log("service_starting", config=self.config.public_dict())
        if self.config.terminal_rich_enabled and self._terminal_task is None:
            from services.sec_gateway.terminal import run_terminal_dashboard

            self._terminal_task = asyncio.create_task(run_terminal_dashboard(self), name="sec-gateway-terminal-dashboard")
        try:
            self._set_phase("preflight", "Checking SEC gateway dependencies.")
            await self.preflight()
            self._set_phase("coverage", "Ensuring and bootstrapping SEC coverage manifest.")
            await asyncio.to_thread(self._prepare_coverage)
            self._set_phase("gap_planning", "Planning SEC startup gaps.")
            await asyncio.to_thread(self._plan_startup_gaps)
            self._start_live_workers()
            self._set_phase("polling", "SEC current feed polling is running.")
            self._poll_task = asyncio.create_task(self._poll_loop(), name="sec-gateway-poll-loop")
            self._log("service_started")
        except Exception as exc:
            self.logger.exception("service_start_failed", exc)
            self._record_error(exc)
            self._set_phase("failed", repr(exc))
            raise

    async def stop(self) -> None:
        self._set_phase("stopping", "Stopping SEC gateway; waiting for live filing workers to drain.")
        self._stop_event.set()
        if self._poll_task is not None:
            try:
                await asyncio.wait_for(self._poll_task, timeout=self.config.graceful_shutdown_seconds)
            except TimeoutError:
                self._poll_task.cancel()
                await asyncio.gather(self._poll_task, return_exceptions=True)
                self.logger.event("poll_loop_cancelled_after_timeout", timeout_seconds=self.config.graceful_shutdown_seconds)
        drained = await self._drain_live_queue("shutdown")
        await self._stop_live_workers(cancel=not drained)
        await asyncio.to_thread(self._finalize_live_coverage)
        if self._terminal_task is not None:
            self._terminal_task.cancel()
            await asyncio.gather(self._terminal_task, return_exceptions=True)
        await self.logger.stop()

    async def preflight(self) -> PreflightReport:
        self.metrics.preflight_status = "running"
        try:
            report = await asyncio.to_thread(run_preflight, self.config)
        except PreflightError as exc:
            self.metrics.preflight_status = exc.report.status
            self.metrics.preflight_checked_at_utc = exc.report.checked_at_utc
            self.metrics.preflight_checks = exc.report.public_dict()["checks"]  # type: ignore[assignment]
            raise
        self.metrics.preflight_status = report.status
        self.metrics.preflight_checked_at_utc = report.checked_at_utc
        self.metrics.preflight_checks = report.public_dict()["checks"]  # type: ignore[assignment]
        return report

    def snapshot_metrics(self) -> dict[str, Any]:
        self._prune_recent_metadata()
        self.metrics.live_queue_size = self._live_queue.qsize()
        self.metrics.pending_shutdown_jobs = self._live_queue.qsize() + self.metrics.live_active_workers
        cooldown_remaining, cooldown_reason = self._limiter.cooldown_status()
        self.metrics.sec_request_cooldown_remaining_seconds = round(cooldown_remaining, 1)
        self.metrics.sec_request_cooldown_reason = cooldown_reason
        return asdict(self.metrics)

    def recent_snapshot(self, limit: int = 100) -> dict[str, Any]:
        self._prune_recent_metadata()
        return {"rows": list(self._recent[:limit]), "limit": limit}

    def feed_url(self) -> str:
        base = self.config.pipeline.feed_url
        if "count=" in base:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}count={self.config.current_feed_count}"

    def _start_live_workers(self) -> None:
        if self._worker_tasks:
            return
        workers = max(1, self.config.live_workers)
        self._worker_tasks = [
            asyncio.create_task(self._live_worker(index), name=f"sec-live-worker-{index}")
            for index in range(1, workers + 1)
        ]
        self.metrics.live_workers = workers
        self._log("live_workers_started", workers=workers, queue_max_items=self.config.live_queue_max_items)

    async def _stop_live_workers(self, *, cancel: bool = False) -> None:
        if not self._worker_tasks:
            return
        if cancel:
            for task in self._worker_tasks:
                task.cancel()
        else:
            for _task in self._worker_tasks:
                await self._live_queue.put(None)
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []
        self.metrics.live_queue_size = self._live_queue.qsize()
        self._log("live_workers_stopped", cancelled=cancel)

    async def _drain_live_queue(self, reason: str) -> bool:
        if self._live_queue.empty() and self.metrics.live_active_workers == 0:
            return True
        self.metrics.last_worker_message = (
            f"Graceful shutdown is waiting for {self._live_queue.qsize():,} queued SEC filing job(s) "
            f"and {self.metrics.live_active_workers:,} active worker(s)."
        )
        self._log("live_queue_drain_started", reason=reason, queue_size=self._live_queue.qsize(), active_workers=self.metrics.live_active_workers)
        try:
            await asyncio.wait_for(self._live_queue.join(), timeout=self.config.graceful_shutdown_seconds)
        except TimeoutError:
            self.metrics.last_worker_message = "Graceful shutdown timed out while waiting for SEC filing workers."
            self._log("live_queue_drain_timeout", reason=reason, queue_size=self._live_queue.qsize(), active_workers=self.metrics.live_active_workers)
            return False
        self.metrics.last_worker_message = "Live filing queue drained."
        self._log("live_queue_drained", reason=reason)
        return True

    async def _live_worker(self, worker_index: int) -> None:
        while True:
            job = await self._live_queue.get()
            try:
                if job is None:
                    return
                await self._process_live_job(worker_index, job)
            finally:
                self._live_queue.task_done()
                self.metrics.live_queue_size = self._live_queue.qsize()

    async def _process_live_job(self, worker_index: int, job: SecLiveJob) -> None:
        self.metrics.live_active_workers += 1
        self.metrics.live_queue_size = self._live_queue.qsize()
        self.metrics.last_worker_message = f"Worker {worker_index} processing {job.item.accession_number}."
        self._log(
            "live_job_started",
            worker_index=worker_index,
            poll_id=job.poll_id,
            accession_number=job.item.accession_number,
            cik=job.item.cik,
            form_type=job.item.form_type,
        )
        try:
            result = await asyncio.to_thread(self._process_item, job.item, set())
            self._record_live_outcome(SecLiveOutcome(item=job.item, poll_id=job.poll_id, result=result), worker_index=worker_index)
        except Exception as exc:  # noqa: BLE001
            self.metrics.live_worker_failures += 1
            self.metrics.failed_filings += 1
            self._record_error(exc)
            self.metrics.last_worker_message = f"Worker {worker_index} failed {job.item.accession_number}: {exc!r}"
            self._live_coverage_errors += 1
            self._write_live_coverage_status(status="running")
            self._complete_poll_coverage_job(job.poll_id, failed=True)
            self.logger.exception(
                "live_job_failed",
                exc,
                worker_index=worker_index,
                poll_id=job.poll_id,
                accession_number=job.item.accession_number,
                cik=job.item.cik,
            )
        finally:
            async with self._inflight_lock:
                self._inflight_accessions.discard(job.item.accession_number)
            self.metrics.live_active_workers = max(0, self.metrics.live_active_workers - 1)
            self.metrics.live_queue_size = self._live_queue.qsize()

    def _record_live_outcome(self, outcome: SecLiveOutcome, *, worker_index: int) -> None:
        result = outcome.result or SecWriteResult(skipped_existing=True)
        if result.skipped_existing:
            self.metrics.skipped_existing += 1
        else:
            self.metrics.written_filings += result.filing_rows
            self.metrics.xbrl_concept_rows += result.xbrl_concept_rows
            self.metrics.xbrl_company_fact_rows += result.xbrl_company_fact_rows
            self.metrics.xbrl_frame_rows += result.xbrl_frame_rows
            self.metrics.xbrl_frame_observation_rows += result.xbrl_frame_observation_rows
        self.metrics.live_completed_filings += 1
        self.metrics.last_accession = outcome.item.accession_number
        self.metrics.last_form_type = outcome.item.form_type
        self.metrics.last_worker_message = (
            f"Worker {worker_index} completed {outcome.item.accession_number}: "
            f"{'skipped existing' if result.skipped_existing else 'written'}."
        )
        self._resolve_last_error(reason="live_job_completed")
        self._remember(outcome.item, result)
        self._log("live_job_completed", worker_index=worker_index, poll_id=outcome.poll_id, accession_number=outcome.item.accession_number, result=asdict(result))
        if not result.skipped_existing:
            self._maybe_run_write_audit(reason="live_worker_write")
        self._complete_poll_coverage_job(outcome.poll_id, failed=False)

    def _complete_poll_coverage_job(self, poll_id: str, *, failed: bool) -> None:
        state = self._pending_poll_coverage.get(poll_id)
        if state is None:
            return
        state.pending_jobs = max(0, state.pending_jobs - 1)
        if failed:
            state.failed_jobs += 1
        if state.pending_jobs:
            return
        self._pending_poll_coverage.pop(poll_id, None)
        self._record_live_coverage(
            state.start_utc,
            state.end_utc,
            state.feed_items,
            state.skipped_existing,
            state.failed_jobs,
            state.metadata,
        )

    def current_poll_seconds(self) -> float:
        status = self._market_status or self.market_status_provider.snapshot()
        self._market_status = status
        return self.config.poll_seconds if status.active_collection_window else self.config.closed_poll_seconds

    def local_extended_session_active(self) -> bool:
        return self.market_status_provider.snapshot().active_collection_window

    def _prepare_coverage(self) -> None:
        ensure_coverage_table(self._client, self._coverage)
        inserted = bootstrap_from_existing_tables(
            self._client,
            self._coverage,
            run_id=self._run_id,
            host_role=self.host_role(),
            source_database=self.config.pipeline.clickhouse.read_database,
        )
        self._log("coverage_bootstrap", inserted=len(inserted))
        if self.config.full_audit_on_startup:
            self._run_write_audit(reason="coverage_prepare")

    def _plan_startup_gaps(self) -> None:
        plan = plan_coverage_gaps(
            self._client,
            self._coverage,
            read_database=self.config.pipeline.clickhouse.read_database,
            now_utc=datetime.now(UTC),
        )
        gaps = plan.gaps
        self.metrics.coverage_interval_count = plan.interval_count
        self.metrics.gap_count = len(gaps)
        if not gaps:
            self.metrics.gap_status = "ok"
            self.metrics.gap_message = f"No SEC coverage gaps detected across {plan.interval_count:,} manifest interval(s)."
            return
        self.metrics.gap_status = "needs_action"
        self.metrics.gap_message = "; ".join(f"{gap.coverage_kind}: {gap.reason} ({gap.days:.1f}d)" for gap in gaps)
        historical = [gap for gap in gaps if gap.coverage_kind != KIND_LIVE_FEED or gap.start_utc.date() < datetime.now(UTC).date()]
        if historical:
            self._write_historical_fill_script(historical)

    def _write_historical_fill_script(self, gaps: list[SecGap]) -> None:
        start = min(gap.start_utc.date() for gap in gaps)
        end = (datetime.now(UTC).date() + timedelta(days=1))
        script_root = workstation_script_write_root(self.config)
        sync_summary = sync_historical_gap_fill_dependencies(self.config, script_root)
        root = script_root / "generated" / "sec_gateway_manual_gap_fill" / self._run_id
        data_root = workstation_script_data_root(self.config)
        prepared_root = data_root / "prepared"
        artifact_root = data_root / "sec_core"
        plans = [
            build_historical_fill_plan(
                start_date=start,
                end_date=end,
                code_root_win=self.config.pipeline.workstation_code_root_win,
                read_database=self.config.pipeline.clickhouse.read_database,
                write_database=self.config.pipeline.clickhouse.write_database,
                extra_args=[
                    "--coverage-table",
                    self.config.pipeline.clickhouse.coverage_table,
                    "--bulk-mirror-database",
                    os.environ.get("SEC_BULK_MIRROR_DATABASE", "sec_core"),
                    "--artifact-root-win",
                    str(artifact_root),
                    "--core-output-root-win",
                    str(prepared_root / "sec_core"),
                    "--output-root-win",
                    str(prepared_root / "sec_historical_gap_fill"),
                    "--daily-archive-output-root-win",
                    str(prepared_root / "sec_daily_feed_archives"),
                    "--archive-validation-output-root-win",
                    str(prepared_root / "sec_downloaded_archive_validation"),
                    "--text-parts-output-root-win",
                    str(prepared_root / "sec_filing_text_parts"),
                    "--xbrl-output-root-win",
                    str(prepared_root / "sec_xbrl_companyfacts_catchup"),
                    "--xbrl-repair-output-root-win",
                    str(prepared_root / "sec_xbrl_integrity_repair"),
                    "--integrity-audit-output-root-win",
                    str(prepared_root / "sec_integrity_audit"),
                    "--parts-root-win",
                    str(data_root),
                    "--parts-root-ch",
                    os.environ.get("SEC_TEXT_PARTS_ROOT_CH", "/mnt/d/market-data"),
                    "--bulk-sources",
                    "submissions,companyfacts",
                    "--bulk-download-concurrency",
                    "2",
                    "--bulk-ingest-batch-size",
                    "50000",
                    "--bulk-insert-max-retries",
                    os.environ.get("SEC_BULK_INSERT_MAX_RETRIES", "12"),
                    "--bulk-insert-retry-base-seconds",
                    os.environ.get("SEC_BULK_INSERT_RETRY_BASE_SECONDS", "5.0"),
                    "--bulk-insert-retry-max-seconds",
                    os.environ.get("SEC_BULK_INSERT_RETRY_MAX_SECONDS", "120.0"),
                    "--archive-download-concurrency",
                    "2",
                    "--archive-validation-workers",
                    "4",
                    "--text-extract-workers",
                    str(max(1, self.config.live_workers)),
                    "--xbrl-workers",
                    str(max(1, self.config.live_workers)),
                    "--sec-request-min-interval-seconds",
                    str(max(0.0, self.config.pipeline.request_min_interval_seconds)),
                    "--request-timeout-seconds",
                    str(max(1.0, self.config.pipeline.request_timeout_seconds)),
                    "--max-retries",
                    "8",
                    "--retry-base-seconds",
                    "30",
                    "--pending-multiplier",
                    "2",
                    "--sample-limit",
                    "1000",
                    "--sample-text-chars",
                    "2000",
                    "--min-text-chars",
                    "40",
                    "--max-text-chars",
                    "0",
                    "--resume-from-coverage",
                ],
                execute=True,
            )
        ]
        # Keep a separate final audit command visible in the generated script.
        # The unified fill already audits internally; this gives the operator a
        # short, final post-run verification surface even if the fill script is
        # later extended with more stages.
        plans.append(
            build_integrity_audit_plan(
                code_root_win=self.config.pipeline.workstation_code_root_win,
                database=self.config.pipeline.clickhouse.write_database,
            )
        )
        script_path = write_multi_plan_script(plans, root / f"{self._run_id}_run_all.ps1")
        run_script_path = workstation_script_run_path(script_path, self.config)
        self.metrics.manual_gap_fill_script_win = str(run_script_path)
        self.metrics.manual_gap_fill_command = "\n".join(plan.command_text for plan in plans)
        self._log(
            "historical_gap_fill_script_written",
            script_path=str(run_script_path),
            script_storage_path=str(script_path),
            dependency_sync=sync_summary,
            commands=[plan.command for plan in plans],
        )
        if backfill_auto_run_allowed(
            is_workstation=self.config.is_workstation,
            execute=self.config.execute,
            auto_run_enabled=self.config.auto_run_historical_on_workstation,
            service_prefix="SEC",
        ):
            process = run_plan_script(script_path, cwd=self.config.pipeline.workstation_code_root_win)
            self.metrics.gap_status = "historical_fill_started"
            self.metrics.gap_message = f"Started workstation historical fill pid={process.pid}."
            self._log("historical_gap_fill_started", pid=process.pid, script_path=str(script_path))
        elif self.config.is_workstation and self.config.auto_run_historical_on_workstation and self.config.execute:
            self.metrics.gap_status = "historical_fill_deferred_market_window"
            self.metrics.gap_message = (
                f"Generated SEC historical fill script but deferred auto-run until {maintenance_window_message('SEC')}: "
                f"{script_path}"
            )
            self._log("historical_gap_fill_deferred", script_path=str(script_path), reason="active_collection_window")

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            started = datetime.now(UTC)
            try:
                await asyncio.to_thread(self.refresh_market_status_if_needed)
                self.metrics.current_poll_seconds = self.current_poll_seconds()
                cooldown_remaining, cooldown_reason = self._limiter.cooldown_status()
                if cooldown_remaining > 0:
                    self.metrics.sec_request_cooldown_remaining_seconds = round(cooldown_remaining, 1)
                    self.metrics.sec_request_cooldown_reason = cooldown_reason
                    self._set_phase(
                        "provider_cooldown",
                        f"SEC provider cooldown active for {cooldown_remaining:.0f}s ({cooldown_reason or 'cooldown'}).",
                    )
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=min(cooldown_remaining, max(1.0, self.current_poll_seconds())))
                    except TimeoutError:
                        pass
                    continue
                await self._poll_once()
            except Exception as exc:  # noqa: BLE001
                self.metrics.poll_failures += 1
                self._record_error(exc)
                self.logger.exception("poll_failed", exc)
                cooldown_remaining, cooldown_reason = self._limiter.cooldown_status()
                if cooldown_remaining > 0:
                    self._set_phase(
                        "provider_cooldown",
                        f"SEC provider transient error; delaying requests for {cooldown_remaining:.0f}s ({cooldown_reason or 'cooldown'}).",
                    )
            elapsed = (datetime.now(UTC) - started).total_seconds()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, self.current_poll_seconds() - elapsed))
            except TimeoutError:
                pass

    async def _poll_once(self) -> None:
        poll_started = datetime.now(UTC)
        poll_id = f"sec_live_{self.metrics.poll_runs + 1:012d}_{uuid.uuid4().hex[:8]}"
        self.metrics.poll_runs += 1
        self.metrics.last_poll_at_utc = poll_started.isoformat(timespec="seconds").replace("+00:00", "Z")
        self._set_phase("poll_fetch", "Fetching SEC current feed.")
        items = await asyncio.to_thread(self._feed.fetch)
        existing = await asyncio.to_thread(self._existing_accessions, items)
        self.metrics.feed_items += len(items)
        skipped = 0
        failed = 0
        queued = 0
        duplicate_active = 0
        self._set_phase("poll_queue", f"Queueing {len(items):,} SEC feed item(s) for live processing.")
        for item in items:
            if item.accession_number in existing:
                skipped += 1
                self._remember(item, SecWriteResult(skipped_existing=True))
                continue
            if await self._register_inflight(item.accession_number):
                await self._live_queue.put(SecLiveJob(item=item, poll_id=poll_id))
                queued += 1
                self.metrics.live_queued_filings += 1
                self.metrics.live_queue_size = self._live_queue.qsize()
                continue
            duplicate_active += 1
        self.metrics.processed_filings += len(items)
        self.metrics.skipped_existing += skipped
        poll_end = datetime.now(UTC)
        if queued:
            self._pending_poll_coverage[poll_id] = SecPollCoverageState(
                poll_id=poll_id,
                start_utc=poll_started,
                end_utc=poll_end,
                feed_items=len(items),
                skipped_existing=skipped,
                pending_jobs=queued,
                metadata={"poll_id": poll_id, "queued": queued, "duplicate_active": duplicate_active},
            )
        elif duplicate_active == 0:
            await asyncio.to_thread(
                self._record_live_coverage,
                poll_started,
                poll_end,
                len(items),
                skipped,
                failed,
                {"poll_id": poll_id, "queued": queued, "duplicate_active": duplicate_active},
            )
        self.metrics.last_worker_message = (
            f"Poll queued={queued:,}, skipped_existing={skipped:,}, active_duplicates={duplicate_active:,}."
        )
        self._set_phase("polling", self.metrics.last_worker_message)
        if queued == 0:
            self._resolve_last_error(reason="poll_complete")
        self._log("poll_complete", poll_id=poll_id, feed_items=len(items), queued=queued, skipped_existing=skipped, duplicate_active=duplicate_active, failed=failed)

    def _finalize_live_coverage(self) -> None:
        if not self.config.execute or self._live_coverage_start_utc is None:
            return
        end = self._live_coverage_end_utc or datetime.now(UTC)
        self._live_coverage_end_utc = end
        insert_coverage(
            self._client,
            self._coverage,
            coverage_id=self._coverage_id,
            coverage_kind=KIND_LIVE_FEED,
            start_utc=self._live_coverage_start_utc,
            end_utc=end,
            status="completed",
            row_count=self._live_coverage_rows,
            error_count=self._live_coverage_errors,
            run_id=self._run_id,
            host_role=self.host_role(),
            metadata={"completed_reason": "gateway_shutdown"},
            completed=True,
        )
        self._log("live_coverage_completed", row_count=self._live_coverage_rows, error_count=self._live_coverage_errors)

    def _existing_accessions(self, items: list[SecFeedItem]) -> set[str]:
        accessions = sorted({item.accession_number for item in items if item.accession_number})
        if not accessions:
            return set()
        values = ",".join(sql_string(item) for item in accessions)
        out = self._client.execute(
            f"""
            SELECT accession_number
            FROM {qi(self.config.pipeline.clickhouse.write_database)}.sec_filing_v2 FINAL
            WHERE accession_number IN ({values})
            FORMAT TSV
            """
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

    async def _register_inflight(self, accession_number: str) -> bool:
        async with self._inflight_lock:
            if accession_number in self._inflight_accessions:
                return False
            self._inflight_accessions.add(accession_number)
            return True

    def _record_live_coverage(
        self,
        start_utc: datetime,
        end_utc: datetime,
        feed_items: int,
        skipped_existing: int,
        failed: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.config.execute:
            return
        if self._live_coverage_start_utc is None:
            self._live_coverage_start_utc = start_utc
        self._live_coverage_end_utc = end_utc
        self._live_coverage_rows += feed_items
        self._live_coverage_errors += failed
        self._write_live_coverage_status(
            status="running",
            metadata={
                "last_feed_items": feed_items,
                "last_skipped_existing": skipped_existing,
                "last_failed": failed,
                **(metadata or {}),
            },
        )

    def _write_live_coverage_status(self, *, status: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.config.execute or self._live_coverage_start_utc is None:
            return
        insert_coverage(
            self._client,
            self._coverage,
            coverage_id=self._coverage_id,
            coverage_kind=KIND_LIVE_FEED,
            start_utc=self._live_coverage_start_utc,
            end_utc=self._live_coverage_end_utc or datetime.now(UTC),
            status=status,
            row_count=self._live_coverage_rows,
            error_count=self._live_coverage_errors,
            run_id=self._run_id,
            host_role=self.host_role(),
            metadata={
                "queued_filings": self.metrics.live_queued_filings,
                "completed_filings": self.metrics.live_completed_filings,
                "worker_failures": self.metrics.live_worker_failures,
                **(metadata or {}),
            },
        )

    def _run_write_audit(self, *, reason: str) -> None:
        try:
            audit = self._writer.audit_integrity()
        except Exception as exc:  # noqa: BLE001
            self.metrics.audit_status = "failed"
            self.metrics.audit_message = repr(exc)
            self._record_error(exc)
            self.logger.exception("write_database_audit_failed", exc, reason=reason)
            return
        self.metrics.audit_status = "ok" if audit.ok else "warn"
        self.metrics.audit_message = (
            f"write_db={self.config.pipeline.clickhouse.write_database} "
            f"filings={audit.filing_rows} documents={audit.document_rows} payloads={audit.payload_rows} texts={audit.text_rows} skips={audit.skip_rows} "
            f"xbrl_facts={audit.xbrl_company_fact_rows} xbrl_frames={audit.xbrl_frame_rows} "
            f"duplicate_filings={audit.duplicate_filing_keys} orphan_documents={audit.documents_without_filing} "
            f"orphan_payload_documents={audit.payloads_without_document} orphan_text_documents={audit.texts_without_document} orphan_text_filings={audit.texts_without_filing} "
            f"orphan_xbrl_facts={audit.company_facts_without_filing} "
            f"orphan_frame_fact={audit.frame_observations_without_company_fact} "
            f"orphan_frame_parent={audit.frame_observations_without_frame_parent}"
        )
        self._log("write_database_audit", reason=reason, ok=audit.ok, audit=asdict(audit))
        self._write_batches_since_audit = 0

    def _maybe_run_write_audit(self, *, reason: str) -> None:
        self._write_batches_since_audit += 1
        cadence = max(0, self.config.full_audit_after_write_batches)
        if cadence and self._write_batches_since_audit >= cadence:
            self._run_write_audit(reason=reason)

    def refresh_market_status_if_needed(self) -> None:
        if not self.config.market_status_enabled:
            self.metrics.market_status_source = "disabled"
            self.metrics.current_poll_seconds = self.current_poll_seconds()
            return
        status = self.market_status_provider.snapshot()
        self._market_status = status
        self.metrics.market_status = status.session or status.market or "unknown"
        self.metrics.market_status_source = status.source
        self.metrics.market_status_server_time = status.server_time
        self.metrics.market_status_updated_at_utc = status.checked_at_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.metrics.market_status_error = status.error
        self.metrics.current_poll_seconds = self.current_poll_seconds()
        self._log(
            "market_status_updated",
            market=status.market,
            early_hours=status.early_hours,
            after_hours=status.after_hours,
            server_time=status.server_time,
            source=status.source,
            reason=status.reason,
            holiday_status=status.holiday_status,
            holiday_name=status.holiday_name,
        )

    def _process_item(self, item: SecFeedItem, existing: set[str]) -> SecWriteResult:
        if item.accession_number in existing:
            return SecWriteResult(skipped_existing=True)
        if not self.config.execute:
            return SecWriteResult(skipped_existing=True)
        rows = self._live_pipeline.process_feed_item(item, source_run_id=self._run_id)
        return self._writer.write_accession(
            filing_row=rows.filing_row,
            document_rows=rows.document_rows,
            payload_rows=rows.payload_rows,
            text_rows=rows.text_rows,
            skip_rows=rows.skip_rows,
            xbrl_concept_rows=rows.xbrl_rows.concept_rows,
            xbrl_company_fact_rows=rows.xbrl_rows.company_fact_rows,
            xbrl_frame_rows=rows.xbrl_rows.frame_rows,
            xbrl_frame_observation_rows=rows.xbrl_rows.frame_observation_rows,
            skip_existing=True,
        )

    def _remember(self, item: SecFeedItem, result: SecWriteResult) -> None:
        self._refresh_cache_metrics()
        row = {
            "accession_number": item.accession_number,
            "cik": item.cik,
            "form_type": item.form_type,
            "title": item.title,
            "updated_at_utc": item.updated_at_utc.isoformat().replace("+00:00", "Z") if item.updated_at_utc else "",
            "status": "skipped_existing" if result.skipped_existing else "written",
            "documents": result.document_rows,
            "payloads": result.payload_rows,
            "texts": result.text_rows,
            "skips": result.skip_rows,
            "xbrl_facts": result.xbrl_company_fact_rows,
        }
        self._recent.insert(0, row)
        self._prune_recent_metadata()

    def _refresh_cache_metrics(self) -> None:
        stats = self._live_pipeline.cache_stats()
        self.metrics.submissions_cache_entries = int(stats.get("submissions_cache_entries") or 0)
        self.metrics.submissions_cache_limit = int(stats.get("submissions_cache_limit") or 0)
        self.metrics.submissions_cache_max_age_seconds = int(stats.get("submissions_cache_max_age_seconds") or 0)
        self.metrics.xbrl_payload_cache_entries = int(stats.get("xbrl_payload_cache_entries") or 0)
        self.metrics.xbrl_payload_cache_limit = int(stats.get("xbrl_payload_cache_limit") or 0)
        self.metrics.xbrl_payload_cache_max_age_seconds = int(stats.get("xbrl_payload_cache_max_age_seconds") or 0)
        self.metrics.xbrl_missing_cik_cache_entries = int(stats.get("xbrl_missing_cik_cache_entries") or 0)
        self.metrics.xbrl_missing_cik_cache_limit = int(stats.get("xbrl_missing_cik_cache_limit") or 0)

    def _prune_recent_metadata(self) -> None:
        retention = timedelta(hours=max(0.0, self.config.recent_metadata_retention_hours))
        if retention.total_seconds() > 0:
            cutoff = datetime.now(UTC) - retention
            self._recent = [row for row in self._recent if recent_row_time(row) is None or recent_row_time(row) >= cutoff]
        del self._recent[250:]
        self.metrics.recent_metadata_rows = len(self._recent)
        self.metrics.recent_metadata_retention_hours = max(0.0, float(self.config.recent_metadata_retention_hours))

    def host_role(self) -> str:
        return "workstation" if self.config.is_workstation else "remote"

    def _set_phase(self, phase: str, message: str) -> None:
        self.metrics.current_phase = phase
        self.metrics.current_phase_message = message
        self._log("phase", phase=phase, message=message)

    def _record_error(self, exc: BaseException) -> None:
        self.metrics.last_error = repr(exc)
        self.metrics.last_error_status = "active"
        self.metrics.last_error_seen_at_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.metrics.last_error_resolved_at_utc = ""

    def _resolve_last_error(self, *, reason: str) -> None:
        if not self.metrics.last_error or self.metrics.last_error_status == "resolved":
            return
        self.metrics.last_error_status = "resolved"
        self.metrics.last_error_resolved_at_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        self._log("last_error_resolved", reason=reason, last_error=self.metrics.last_error)

    def _log(self, event: str, **payload: Any) -> None:
        self.logger.event(event, **payload)


def workstation_script_data_root(config: SecGatewayConfig) -> Path:
    if config.is_workstation:
        return config.pipeline.data_root_win
    return Path("D:/market-data")


def workstation_script_write_root(config: SecGatewayConfig) -> Path:
    if config.is_workstation:
        return config.pipeline.workstation_code_root_win
    explicit = os.environ.get("SEC_GATEWAY_WORKSTATION_SHARE_CODE_ROOT_WIN", "").strip()
    if explicit:
        return Path(explicit)
    return WORKSTATION_SHARE_CODE_ROOT_WIN


def workstation_script_run_path(script_path: Path, config: SecGatewayConfig) -> Path:
    if config.is_workstation:
        return script_path
    try:
        relative = script_path.relative_to(workstation_script_write_root(config))
    except ValueError:
        return script_path
    return config.pipeline.workstation_code_root_win / relative


def sync_historical_gap_fill_dependencies(config: SecGatewayConfig, target_code_root: Path) -> dict[str, Any]:
    source_root = REPO_ROOT
    target_root = target_code_root
    if same_path(source_root, target_root):
        return {"status": "skipped", "reason": "source_and_target_are_same", "target": str(target_root)}

    copied: list[str] = []
    for relative_dir in [
        Path("pipelines/sec/edgar"),
        Path("research/mlops"),
    ]:
        copy_dependency_tree(source_root / relative_dir, target_root / relative_dir)
        copied.append(str(relative_dir).replace("\\", "/"))

    for relative_file in [
        Path("pipelines/__init__.py"),
        Path("pipelines/sec/__init__.py"),
        Path("research/__init__.py"),
    ]:
        copy_dependency_file(source_root / relative_file, target_root / relative_file)
        copied.append(str(relative_file).replace("\\", "/"))

    return {"status": "ok", "source": str(source_root), "target": str(target_root), "copied": copied}


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve().samefile(right.resolve())
    except (FileNotFoundError, OSError, ValueError):
        return str(left).rstrip("\\/").lower() == str(right).rstrip("\\/").lower()


def copy_dependency_tree(source: Path, target: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"SEC historical dependency source directory is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"),
    )


def copy_dependency_file(source: Path, target: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"SEC historical dependency source file is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def recent_row_time(row: dict[str, Any]) -> datetime | None:
    text = str(row.get("updated_at_utc") or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
