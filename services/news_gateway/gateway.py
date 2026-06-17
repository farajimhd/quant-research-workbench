from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipelines.news.benzinga.news_benzinga_normalize import artifact_path_for_payload, parse_provider_datetime, write_raw_payload
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline, ProcessedNewsItem
from pipelines.news.benzinga.news_pipeline.provider import BenzingaProviderClient, BenzingaProviderConfig
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident
from services.news_gateway.config import NewsGatewayConfig
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
    last_cycle_status: str = ""
    last_cycle_provider_rows: int = 0
    last_cycle_processed_rows: int = 0
    last_cycle_written_rows: int = 0
    last_cycle_skipped_existing: int = 0
    last_cycle_wall_seconds: float = 0.0
    current_poll_seconds: float = 0.0


class NewsGateway:
    def __init__(self, config: NewsGatewayConfig) -> None:
        self.config = config
        self.state = NewsMemoryState(config.recent_history_limit)
        self.metrics = GatewayMetrics()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._gap_task: asyncio.Task[None] | None = None
        self._terminal_task: asyncio.Task[None] | None = None
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
            password=clickhouse_password(),
            database=config.clickhouse_database,
            normalized_table=config.normalized_table,
            ticker_table=config.ticker_table,
        )
        self.provider = BenzingaProviderClient(
            BenzingaProviderConfig(
                endpoint_url=config.benzinga_url,
                api_key=massive_api_key(),
                page_limit=config.page_limit,
                max_pages=config.max_pages,
            )
        )

    async def start(self) -> None:
        self.config.raw_root_win.mkdir(parents=True, exist_ok=True)
        self.config.prepared_root_win.mkdir(parents=True, exist_ok=True)
        await self._plan_startup_gap()
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

    async def _plan_startup_gap(self) -> None:
        latest = await asyncio.to_thread(self._latest_persisted_published_at)
        now = datetime.now(UTC)
        if latest is None:
            self.metrics.gap_status = "no_watermark"
            self.metrics.gap_message = "No existing persisted news watermark found; live polling will use normal lookback."
            return
        gap_start = latest - timedelta(seconds=max(0, self.config.poll_overlap_seconds))
        gap_seconds = max(0.0, (now - gap_start).total_seconds())
        threshold_seconds = self.config.restart_gap_max_days * 86_400
        if gap_seconds <= self.config.lookback_minutes * 60:
            self.metrics.gap_status = "covered_by_live_lookback"
            self.metrics.gap_message = f"Latest persisted news is recent enough: {latest.isoformat()}."
            return
        if gap_seconds <= threshold_seconds:
            self.metrics.gap_status = "auto_started"
            self.metrics.gap_message = f"Startup gap from {gap_start.isoformat()} to {now.isoformat()} will be filled in background."
            self._gap_task = asyncio.create_task(self._fill_gap(gap_start, now), name="benzinga-news-startup-gap-fill")
            return
        command = historical_gap_command(gap_start, now, self.config)
        self.metrics.manual_gap_fill_command = command
        if self.config.is_workstation:
            self.metrics.gap_status = "workstation_auto_started_large_gap"
            self.metrics.gap_message = f"Large startup gap is {gap_seconds / 86400:.2f} days; workstation run will fill automatically in background."
            self._gap_task = asyncio.create_task(self._fill_gap(gap_start, now), name="benzinga-news-large-gap-fill")
        else:
            self.metrics.gap_status = "manual_required_large_gap"
            self.metrics.gap_message = (
                f"Large startup gap is {gap_seconds / 86400:.2f} days. Run the provided historical fill command on the workstation."
            )
            print(self.metrics.gap_message, flush=True)
            print(command, flush=True)

    async def _fill_gap(self, start_utc: datetime, end_utc: datetime) -> None:
        current = start_utc
        while current < end_utc and not self._stop_event.is_set():
            chunk_end = min(current + timedelta(hours=6), end_utc)
            await self.poll_window(current, chunk_end)
            current = chunk_end
        self.metrics.gap_status = "auto_completed"

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            end_utc = datetime.now(UTC)
            start_utc = end_utc - timedelta(minutes=max(1, self.config.lookback_minutes))
            await self.poll_window(start_utc, end_utc)
            sleep_seconds = self.current_poll_seconds()
            self.metrics.current_poll_seconds = sleep_seconds
            await asyncio.sleep(sleep_seconds)

    async def poll_window(self, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
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
            self.metrics.last_cycle_status = "ok" if failed == 0 else "completed_with_errors"
            self.metrics.last_cycle_provider_rows = len(fetch_result.items)
            self.metrics.last_cycle_processed_rows = len(processed)
            self.metrics.last_cycle_written_rows = write_summary.normalized_rows_inserted
            self.metrics.last_cycle_skipped_existing = write_summary.skipped_existing
            self.metrics.last_cycle_wall_seconds = time.perf_counter() - started
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

    def _latest_persisted_published_at(self) -> datetime | None:
        client = ClickHouseHttpClient(self.target.url, self.target.user, self.target.password)
        sql = (
            f"SELECT max(published_at_utc) AS ts FROM {quote_ident(self.target.database)}.{quote_ident(self.target.normalized_table)} "
            "FORMAT JSONEachRow"
        )
        try:
            text = client.execute(sql)
        except Exception as exc:  # noqa: BLE001
            self.metrics.last_error = f"latest_persisted_query_failed: {exc!r}"
            return None
        for line in text.splitlines():
            if not line.strip():
                continue
            value = json.loads(line).get("ts")
            if not value:
                return None
            return parse_clickhouse_dt64(str(value))
        return None


def save_raw_payload(raw_root: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    try:
        published = parse_provider_datetime(str(payload.get("published") or ""))
    except Exception:
        published = datetime.now(UTC)
    raw_path = artifact_path_for_payload(raw_root.parent, payload, published)
    raw_hash = write_raw_payload(raw_path, payload)
    return raw_path, raw_hash


def parse_clickhouse_dt64(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T") + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def historical_gap_command(start_utc: datetime, end_utc: datetime, config: NewsGatewayConfig) -> str:
    return (
        "python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill "
        f"--start-utc {start_utc.isoformat().replace('+00:00', 'Z')} "
        f"--end-utc {end_utc.isoformat().replace('+00:00', 'Z')} "
        f"--raw-root-win {quote_arg(str(config.raw_root_win))} "
        "--bucket-minutes 90 --workers 4 --batch-size 1000 --progress-interval 10 --execute"
    )


def quote_arg(value: str) -> str:
    return f'"{value}"' if " " in value or "\\" in value else value


def massive_api_key() -> str:
    value = __import__("os").environ.get("MASSIVE_API_KEY", "").strip()
    if not value:
        raise RuntimeError("MASSIVE_API_KEY is required")
    return value


def clickhouse_password() -> str:
    import os

    return (
        os.environ.get("NEWS_CLICKHOUSE_PASSWORD", "").strip()
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD", "").strip()
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "").strip()
    )
