from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import SecClickHouseWriter, SecWriteResult, qi, sql_string
from pipelines.sec.edgar.sec_pipeline.coverage import (
    KIND_LIVE_FEED,
    SecCoverageConfig,
    SecGap,
    bootstrap_from_existing_tables,
    ensure_coverage_table,
    insert_coverage,
    new_coverage_id,
    plan_freshness_gaps,
)
from pipelines.sec.edgar.sec_pipeline.feed import SecCurrentFeedClient, SecFeedItem
from pipelines.sec.edgar.sec_pipeline.historical_fill import build_historical_fill_plan, run_historical_fill, write_plan_script
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient
from pipelines.sec.edgar.sec_pipeline.live_pipeline import SecLiveFilingPipeline
from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter
from research.mlops.clickhouse import ClickHouseHttpClient
from services.news_gateway.run_logger import AsyncRunLogger
from services.sec_gateway.config import SecGatewayConfig
from services.sec_gateway.preflight import PreflightError, PreflightReport, run_preflight


EASTERN = ZoneInfo("America/New_York")


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
    last_accession: str = ""
    last_form_type: str = ""
    gap_status: str = "not_started"
    gap_message: str = ""
    gap_count: int = 0
    manual_gap_fill_script_win: str = ""
    manual_gap_fill_command: str = ""
    audit_status: str = "not_started"
    audit_message: str = ""
    run_log_path: str = ""


class SecGateway:
    def __init__(self, config: SecGatewayConfig) -> None:
        self.config = config
        self.metrics = SecGatewayMetrics()
        self._run_id = f"sec_gateway_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._terminal_task: asyncio.Task[None] | None = None
        self._recent: list[dict[str, Any]] = []
        self._coverage_id = new_coverage_id("sec_live_feed")
        self._live_coverage_start_utc: datetime | None = None
        self._live_coverage_end_utc: datetime | None = None
        self._live_coverage_rows = 0
        self._live_coverage_errors = 0
        self.logger = AsyncRunLogger(
            root=config.pipeline.prepared_root_win / "sec_gateway" / "logs",
            run_id=self._run_id,
            enabled=config.run_log_enabled,
            queue_size=config.run_log_queue_size,
        )
        self.logger.path = self.logger.path.with_name("sec_gateway_events.jsonl")
        self.metrics.run_log_path = str(self.logger.path) if config.run_log_enabled else ""
        self._client = ClickHouseHttpClient(config.pipeline.clickhouse.url, config.pipeline.clickhouse.user, config.pipeline.clickhouse.password)
        self._coverage = SecCoverageConfig(
            database=config.pipeline.clickhouse.write_database,
            coverage_table=config.pipeline.clickhouse.coverage_table,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        self._writer = SecClickHouseWriter(self._client, database=config.pipeline.clickhouse.write_database)
        self._limiter = SecRateLimiter(config.pipeline.request_min_interval_seconds)
        self._http = SecHttpClient(user_agent=config.pipeline.sec_user_agent, rate_limiter=self._limiter, timeout_seconds=config.pipeline.request_timeout_seconds)
        self._feed = SecCurrentFeedClient(feed_url=self.feed_url(), http=self._http)
        self._live_pipeline = SecLiveFilingPipeline(http=self._http, raw_root_win=config.pipeline.raw_live_root_win)

    async def start(self) -> None:
        await self.logger.start()
        self._log("service_starting", config=self.config.public_dict())
        try:
            self._set_phase("preflight", "Checking SEC gateway dependencies.")
            await self.preflight()
            self._set_phase("coverage", "Ensuring and bootstrapping SEC coverage manifest.")
            await asyncio.to_thread(self._prepare_coverage)
            self._set_phase("gap_planning", "Planning SEC startup gaps.")
            await asyncio.to_thread(self._plan_startup_gaps)
            self._set_phase("polling", "SEC current feed polling is running.")
            self._poll_task = asyncio.create_task(self._poll_loop(), name="sec-gateway-poll-loop")
            if self.config.terminal_rich_enabled:
                from services.sec_gateway.terminal import run_terminal_dashboard

                self._terminal_task = asyncio.create_task(run_terminal_dashboard(self), name="sec-gateway-terminal-dashboard")
            self._log("service_started")
        except Exception as exc:
            self.logger.exception("service_start_failed", exc)
            self.metrics.last_error = repr(exc)
            self._set_phase("failed", repr(exc))
            raise

    async def stop(self) -> None:
        self._set_phase("stopping", "Stopping SEC gateway.")
        self._stop_event.set()
        if self._terminal_task is not None:
            self._terminal_task.cancel()
            await asyncio.gather(self._terminal_task, return_exceptions=True)
        if self._poll_task is not None:
            try:
                await asyncio.wait_for(self._poll_task, timeout=self.config.graceful_shutdown_seconds)
            except TimeoutError:
                self._poll_task.cancel()
                await asyncio.gather(self._poll_task, return_exceptions=True)
                self.logger.event("poll_loop_cancelled_after_timeout", timeout_seconds=self.config.graceful_shutdown_seconds)
        await asyncio.to_thread(self._finalize_live_coverage)
        await self.logger.stop()

    async def preflight(self) -> PreflightReport:
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
        return asdict(self.metrics)

    def recent_snapshot(self, limit: int = 100) -> dict[str, Any]:
        return {"rows": list(self._recent[:limit]), "limit": limit}

    def feed_url(self) -> str:
        base = self.config.pipeline.feed_url
        if "count=" in base:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}count={self.config.current_feed_count}"

    def current_poll_seconds(self) -> float:
        now_et = datetime.now(EASTERN).time()
        if dt_time(4, 0) <= now_et <= dt_time(20, 0):
            return self.config.poll_seconds
        return self.config.closed_poll_seconds

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
        self._run_write_audit(reason="coverage_prepare")

    def _plan_startup_gaps(self) -> None:
        gaps = plan_freshness_gaps(self._client, database=self.config.pipeline.clickhouse.read_database, now_utc=datetime.now(UTC))
        self.metrics.gap_count = len(gaps)
        if not gaps:
            self.metrics.gap_status = "ok"
            self.metrics.gap_message = "No SEC freshness gaps detected."
            return
        self.metrics.gap_status = "needs_action"
        self.metrics.gap_message = "; ".join(f"{gap.coverage_kind}: {gap.reason} ({gap.days:.1f}d)" for gap in gaps)
        historical = [gap for gap in gaps if gap.coverage_kind != KIND_LIVE_FEED or gap.start_utc.date() < datetime.now(UTC).date()]
        if historical:
            self._write_historical_fill_script(historical)

    def _write_historical_fill_script(self, gaps: list[SecGap]) -> None:
        start = min(gap.start_utc.date() for gap in gaps)
        end = (datetime.now(UTC).date() + timedelta(days=1))
        root = self.config.pipeline.workstation_code_root_win / "generated" / "sec_gateway_manual_gap_fill" / self._run_id
        plan = build_historical_fill_plan(start_date=start, end_date=end, code_root_win=self.config.pipeline.workstation_code_root_win, execute=True)
        script_path = write_plan_script(plan, root / f"{self._run_id}_run_all.ps1")
        self.metrics.manual_gap_fill_script_win = str(script_path)
        self.metrics.manual_gap_fill_command = plan.command_text
        self._log("historical_gap_fill_script_written", script_path=str(script_path), command=plan.command)
        if self.config.is_workstation and self.config.auto_run_historical_on_workstation and self.config.execute:
            process = run_historical_fill(plan, cwd=self.config.pipeline.workstation_code_root_win)
            self.metrics.gap_status = "historical_fill_started"
            self.metrics.gap_message = f"Started workstation historical fill pid={process.pid}."
            self._log("historical_gap_fill_started", pid=process.pid, command=plan.command)

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            started = datetime.now(UTC)
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception as exc:  # noqa: BLE001
                self.metrics.poll_failures += 1
                self.metrics.last_error = repr(exc)
                self.logger.exception("poll_failed", exc)
            elapsed = (datetime.now(UTC) - started).total_seconds()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, self.current_poll_seconds() - elapsed))
            except TimeoutError:
                pass

    def _poll_once(self) -> None:
        poll_started = datetime.now(UTC)
        self.metrics.poll_runs += 1
        self.metrics.last_poll_at_utc = poll_started.isoformat(timespec="seconds").replace("+00:00", "Z")
        items = self._feed.fetch()
        existing = self._existing_accessions(items)
        self.metrics.feed_items += len(items)
        written = 0
        skipped = 0
        failed = 0
        for item in items:
            try:
                result = self._process_item(item, existing)
                if result.skipped_existing:
                    skipped += 1
                else:
                    written += result.filing_rows
                self.metrics.last_accession = item.accession_number
                self.metrics.last_form_type = item.form_type
                self._remember(item, result)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.logger.exception("filing_process_failed", exc, accession_number=item.accession_number, cik=item.cik)
        self.metrics.processed_filings += len(items)
        self.metrics.written_filings += written
        self.metrics.skipped_existing += skipped
        self.metrics.failed_filings += failed
        if self.config.execute and (written or skipped or failed):
            now = datetime.now(UTC)
            if self._live_coverage_start_utc is None:
                self._live_coverage_start_utc = poll_started
            self._live_coverage_end_utc = now
            self._live_coverage_rows += written + skipped
            self._live_coverage_errors += failed
            insert_coverage(
                self._client,
                self._coverage,
                coverage_id=self._coverage_id,
                coverage_kind=KIND_LIVE_FEED,
                start_utc=self._live_coverage_start_utc,
                end_utc=now,
                status="running",
                row_count=self._live_coverage_rows,
                error_count=self._live_coverage_errors,
                run_id=self._run_id,
                host_role=self.host_role(),
                metadata={"last_feed_items": len(items), "last_written": written, "last_skipped_existing": skipped, "last_failed": failed},
            )
        self._log("poll_complete", feed_items=len(items), written=written, skipped_existing=skipped, failed=failed)
        if written:
            self._run_write_audit(reason="poll_write")

    def _finalize_live_coverage(self) -> None:
        if not self.config.execute or self._live_coverage_start_utc is None:
            return
        end = self._live_coverage_end_utc or datetime.now(UTC)
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

    def _run_write_audit(self, *, reason: str) -> None:
        try:
            audit = self._writer.audit_integrity()
        except Exception as exc:  # noqa: BLE001
            self.metrics.audit_status = "failed"
            self.metrics.audit_message = repr(exc)
            self.logger.exception("write_database_audit_failed", exc, reason=reason)
            return
        self.metrics.audit_status = "ok" if audit.ok else "warn"
        self.metrics.audit_message = (
            f"write_db={self.config.pipeline.clickhouse.write_database} "
            f"filings={audit.filing_rows} documents={audit.document_rows} texts={audit.text_rows} skips={audit.skip_rows} "
            f"duplicate_filings={audit.duplicate_filing_keys} orphan_documents={audit.documents_without_filing} "
            f"orphan_text_documents={audit.texts_without_document} orphan_text_filings={audit.texts_without_filing}"
        )
        self._log("write_database_audit", reason=reason, ok=audit.ok, audit=asdict(audit))

    def _process_item(self, item: SecFeedItem, existing: set[str]) -> SecWriteResult:
        if item.accession_number in existing:
            return SecWriteResult(skipped_existing=True)
        if not self.config.execute:
            return SecWriteResult(skipped_existing=True)
        rows = self._live_pipeline.process_feed_item(item, source_run_id=self._run_id)
        return self._writer.write_accession(
            filing_row=rows.filing_row,
            document_rows=rows.document_rows,
            text_rows=rows.text_rows,
            skip_rows=rows.skip_rows,
            skip_existing=True,
        )

    def _remember(self, item: SecFeedItem, result: SecWriteResult) -> None:
        row = {
            "accession_number": item.accession_number,
            "cik": item.cik,
            "form_type": item.form_type,
            "title": item.title,
            "updated_at_utc": item.updated_at_utc.isoformat().replace("+00:00", "Z") if item.updated_at_utc else "",
            "status": "skipped_existing" if result.skipped_existing else "written",
            "documents": result.document_rows,
            "texts": result.text_rows,
            "skips": result.skip_rows,
        }
        self._recent.insert(0, row)
        del self._recent[250:]

    def host_role(self) -> str:
        return "workstation" if self.config.is_workstation else "remote"

    def _set_phase(self, phase: str, message: str) -> None:
        self.metrics.current_phase = phase
        self.metrics.current_phase_message = message
        self._log("phase", phase=phase, message=message)

    def _log(self, event: str, **payload: Any) -> None:
        self.logger.event(event, **payload)
