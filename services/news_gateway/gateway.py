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
    ensure_coverage_manifest_table,
    find_coverage_gaps,
    insert_coverage_snapshot,
    load_coverage_intervals,
    new_run_id,
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
    preflight_status: str = "not_started"
    preflight_checked_at_utc: str = ""
    preflight_checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GapFillInterval:
    start_utc: datetime
    end_utc: datetime


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
        self._clickhouse_password = default_clickhouse_password()
        self._massive_api_key = massive_api_key()
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
        await self.preflight()
        await self._prepare_coverage_manifest()
        await self._plan_startup_gap()
        await self._open_live_coverage()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="benzinga-news-poll-loop")
        if self.config.terminal_rich_enabled:
            from services.news_gateway.terminal import run_terminal_dashboard

            self._terminal_task = asyncio.create_task(run_terminal_dashboard(self), name="benzinga-news-terminal-dashboard")

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = [task for task in [self._poll_task, self._gap_task, self._terminal_task] if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_live_coverage()

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
            raise
        self._record_preflight_report(report)
        return report

    def _record_preflight_report(self, report: PreflightReport) -> None:
        self._preflight_report = report
        self.metrics.preflight_status = report.status
        self.metrics.preflight_checked_at_utc = report.checked_at_utc
        self.metrics.preflight_checks = [asdict(check) for check in report.checks]

    async def _plan_startup_gap(self) -> None:
        now = datetime.now(UTC)
        intervals = await asyncio.to_thread(self._load_coverage_intervals)
        if not intervals:
            self.metrics.gap_status = "no_watermark"
            self.metrics.gap_message = "No coverage manifest intervals found; live polling will use normal lookback."
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
            return
        largest_gap_seconds = max(gap.seconds for gap in gaps)
        total_gap_seconds = sum(gap.seconds for gap in gaps)
        threshold_seconds = self.config.startup_auto_fill_max_gap_days * 86_400
        if total_gap_seconds <= threshold_seconds:
            self.metrics.gap_status = "auto_started"
            self.metrics.gap_message = (
                f"{len(gaps)} coverage gap(s), total {total_gap_seconds / 86400:.2f} days, "
                "will be filled in background during startup."
            )
            print(self.metrics.gap_message, flush=True)
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
                f"{len(gaps)} coverage gap(s) found; total {total_gap_seconds / 86400:.2f} days, "
                f"largest {largest_gap_seconds / 86400:.2f} days. Running the generated workstation gap-fill script: "
                f"{plan.workstation_script_path}"
            )
            print(self.metrics.gap_message, flush=True)
            print(f"manifest: {plan.workstation_manifest_path}", flush=True)
            self._gap_task = asyncio.create_task(self._run_workstation_gap_fill_plan(plan), name="benzinga-news-workstation-gap-fill")
            return
        self.metrics.gap_status = "manual_required_large_gap"
        self.metrics.gap_message = (
            f"{len(gaps)} coverage gap(s) found; total {total_gap_seconds / 86400:.2f} days, "
            f"largest {largest_gap_seconds / 86400:.2f} days. Run the generated script on the workstation: "
            f"{plan.workstation_script_path}"
        )
        print(self.metrics.gap_message, flush=True)
        print(f"manifest: {plan.workstation_manifest_path}", flush=True)

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
            print(self.metrics.gap_message, flush=True)
            await self._fill_gap(gap.start_utc, gap.end_utc)
        self.metrics.gap_status = "auto_completed"
        self.metrics.gap_message = f"Startup coverage gap fill completed for {total} gap(s)."

    async def _fill_gap(self, start_utc: datetime, end_utc: datetime) -> None:
        current = start_utc
        while current < end_utc and not self._stop_event.is_set():
            chunk_end = min(current + timedelta(minutes=max(1, self.config.gap_fill_chunk_minutes)), end_utc)
            print(f"provider_gap_fill_window={current.isoformat()}->{chunk_end.isoformat()}", flush=True)
            await self.poll_window(current, chunk_end, coverage_mode="gap_fill")
            current = chunk_end

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
                print(line, flush=True)
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
            await asyncio.sleep(sleep_seconds)

    async def poll_window(self, start_utc: datetime, end_utc: datetime, *, coverage_mode: str = "live") -> dict[str, Any]:
        started = time.perf_counter()
        self.metrics.poll_runs += 1
        self.metrics.last_poll_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            fetch_result = await asyncio.to_thread(self.provider.fetch_window, start_utc, end_utc)
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
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.metrics.last_error = repr(exc)
            write_summary = self._write_processed(processed)
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
        )

    async def _prepare_coverage_manifest(self) -> None:
        if not self.config.execute:
            return
        summary = await asyncio.to_thread(self._ensure_and_bootstrap_coverage_manifest)
        if summary.executed:
            message = (
                "Coverage manifest bootstrap completed: "
                f"chunk={summary.chunk_seconds}s non_empty_buckets={summary.non_empty_buckets:,} "
                f"covered_intervals={summary.covered_intervals:,} "
                f"discovered_gap_intervals={summary.discovered_gap_intervals:,} "
                f"discovered_gap_days={summary.discovered_gap_seconds / 86400:.2f}."
            )
            self.metrics.gap_status = "coverage_bootstrapped"
            self.metrics.gap_message = message
            print(message, flush=True)

    def _ensure_and_bootstrap_coverage_manifest(self) -> CoverageBootstrapSummary:
        client = self._coverage_client()
        config = self._coverage_config()
        ensure_coverage_manifest_table(client, config)
        return bootstrap_coverage_from_normalized_table(
            client,
            config,
            chunk_seconds=self.config.coverage_discovery_chunk_seconds,
            force_rebuild=self.config.rebuild_coverage_manifest,
        )

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
            await asyncio.to_thread(
                self._write_completed_gap_coverage,
                start_utc,
                end_utc,
                provider_rows,
                processed_rows,
                written_rows,
                skipped_existing,
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

    def _write_completed_gap_coverage(
        self,
        start_utc: datetime,
        end_utc: datetime,
        provider_rows: int,
        processed_rows: int,
        written_rows: int,
        skipped_existing: int,
    ) -> None:
        self._gap_coverage_counter += 1
        now = datetime.now(UTC)
        snapshot = CoverageSnapshot(
            coverage_id=f"{self._run_id}_gap_{self._gap_coverage_counter:06d}",
            run_id=self._run_id,
            source="gateway_gap_fill",
            status="completed",
            coverage_start_utc=start_utc,
            coverage_end_utc=end_utc,
            started_at_utc=now,
            updated_at_utc=now,
            closed_at_utc=now,
            poll_runs=1,
            provider_rows=provider_rows,
            processed_rows=processed_rows,
            written_rows=written_rows,
            skipped_existing=skipped_existing,
            metadata={"mode": "startup_gap_fill", "chunk_minutes": self.config.gap_fill_chunk_minutes},
        )
        insert_coverage_snapshot(self._coverage_client(), self._coverage_config(), snapshot)

    def _coverage_client(self) -> ClickHouseHttpClient:
        return ClickHouseHttpClient(self.target.url, self.target.user, self.target.password)

    def _coverage_config(self) -> CoverageManifestConfig:
        return CoverageManifestConfig(
            database=self.target.database,
            coverage_table=self.target.coverage_table,
            normalized_table=self.target.normalized_table,
            storage_policy=__import__("os").environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )


def save_raw_payload(raw_root: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    try:
        published = parse_provider_datetime(str(payload.get("published") or ""))
    except Exception:
        published = datetime.now(UTC)
    raw_path = artifact_path_for_payload(raw_root.parent, payload, published)
    raw_hash = write_raw_payload(raw_path, payload)
    return raw_path, raw_hash


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
