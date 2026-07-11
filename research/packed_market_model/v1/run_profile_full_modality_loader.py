from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.packed_market.streaming import (
    ActiveQueryRegistry,
    ClickHouseTickerStreamConfig,
    build_block_jobs,
    build_packed_block_from_events,
    cancel_process_clickhouse_queries as cancel_stream_queries,
    fetch_event_frame,
    load_ticker_month_plans,
)
from research.mlops.rolling_loader.daily_index_cache import month_window
from research.mlops.rolling_loader.daily_index_context import (
    _build_intraday_base_bars,
    _build_intraday_condition_events,
    _query_corporate_actions,
    _query_daily_bars,
    _query_market_news,
    _query_sec_tokens,
    _query_ticker_news,
    _query_xbrl,
    cancel_process_clickhouse_queries as cancel_context_queries,
)
from research.packed_market_model.v1.config import parse_csv


DEFAULT_SCANNER_CACHE_ROOT = Path(r"D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02")


@dataclass(slots=True)
class StepResult:
    name: str
    status: str
    seconds: float
    rows: int = 0
    bytes: int = 0
    columns: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class ProfileState:
    run_dir: Path
    blocks_done: int = 0
    steps_done: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.perf_counter)
    last_message: str = "starting"
    last_steps: list[dict[str, Any]] = field(default_factory=list)

    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at


@dataclass(slots=True)
class CachedPayloadRecord:
    payload: Any
    rows: int
    columns: int
    bytes: int
    detail: dict[str, Any]


@dataclass(slots=True)
class FullProfileSharedCache:
    records: dict[tuple[str, ...], CachedPayloadRecord] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def get(self, key: tuple[str, ...]) -> CachedPayloadRecord | None:
        with self.lock:
            return self.records.get(key)

    def put(self, key: tuple[str, ...], record: CachedPayloadRecord) -> None:
        with self.lock:
            self.records[key] = record

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "items": len(self.records),
                "bytes": sum(int(record.bytes) for record in self.records.values()),
                "keys": ["|".join(key) for key in list(self.records.keys())[:20]],
            }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile full packed-market data loading across all available modalities.")
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker subset. Empty uses the most active ticker plans.")
    parser.add_argument("--max-blocks", type=int, default=4)
    parser.add_argument("--max-plans", type=int, default=24)
    parser.add_argument("--block-sampling", choices=("round-robin", "sequential"), default="round-robin")
    parser.add_argument("--target-origin-count-per-block", type=int, default=65_536)
    parser.add_argument("--event-context-rows", type=int, default=1_024)
    parser.add_argument("--future-event-guard-rows", type=int, default=262_144)
    parser.add_argument("--context-workers", type=int, default=8)
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--max-threads-per-query", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="32G")
    parser.add_argument("--scanner-cache-root", default=str(DEFAULT_SCANNER_CACHE_ROOT))
    parser.add_argument("--require-scanner", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--ticker-news-prior-items", type=int, default=64)
    parser.add_argument("--market-news-prior-items", type=int, default=512)
    parser.add_argument("--sec-filing-prior-items", type=int, default=32)
    parser.add_argument("--xbrl-prior-rows", type=int, default=4096)
    parser.add_argument("--corporate-action-label-days", default="1,2,3,7,28")
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\packed_market_model\v1\profiles")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="rich")
    parser.add_argument("--strict-modalities", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    run_dir = Path(args.output_root) / f"full_modality_profile_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "profile.jsonl"
    state = ProfileState(run_dir=run_dir)
    append_jsonl(report_path, {"event": "start", "args": vars(args), "run_dir": str(run_dir)})
    print(f"FULL MODALITY PROFILE {run_dir}", flush=True)
    print(json.dumps(vars(args), sort_keys=True), flush=True)

    stream_config = ClickHouseTickerStreamConfig(
        months=parse_csv(args.months),
        tickers=parse_csv(args.tickers),
        database=str(args.database),
        events_table_base=str(args.events_table_base),
        events_ticker_day_index_table=str(args.events_ticker_day_index_table),
        target_origin_count_per_block=int(args.target_origin_count_per_block),
        event_context_rows=int(args.event_context_rows),
        future_event_guard_rows=int(args.future_event_guard_rows),
        max_plans=max(0, int(args.max_plans)),
        max_threads_per_query=int(args.max_threads_per_query),
        max_memory_usage=str(args.max_memory_usage),
    )
    stream_queries = ActiveQueryRegistry(prefix=f"packed_full_profile_{time.strftime('%H%M%S')}_")
    context_client_opts = {
        "clickhouse_url": stream_config.clickhouse_url,
        "user": stream_config.user,
        "password": stream_config.password,
        "query_retries": str(stream_config.query_retries),
        "query_retry_backoff_seconds": str(stream_config.query_retry_backoff_seconds),
    }
    rolling_config = RollingMarketDataConfig(
        database=str(args.database),
        sec_context_database=str(args.database),
        events_table=str(args.events_table_base),
        max_threads=int(args.max_threads_per_query),
        max_memory_usage=str(args.max_memory_usage),
    )
    ctx_args = argparse.Namespace(
        skip_token_contexts=False,
        skip_xbrl=False,
        skip_corporate_actions=False,
        ticker_news_prior_items=int(args.ticker_news_prior_items),
        market_news_prior_items=int(args.market_news_prior_items),
        sec_filing_prior_items=int(args.sec_filing_prior_items),
        xbrl_prior_rows=int(args.xbrl_prior_rows),
        corporate_action_label_days=str(args.corporate_action_label_days),
    )
    shared_cache = FullProfileSharedCache()

    try:
        plan_step = time.perf_counter()
        plans = load_ticker_month_plans(stream_config, active_queries=stream_queries)
        if not plans:
            raise RuntimeError("No ticker/month plans discovered for the requested months/tickers.")
        append_jsonl(report_path, {"event": "plans", "seconds": time.perf_counter() - plan_step, "plans": len(plans), "first_plans": [plan.ticker for plan in plans[:12]]})
        state.last_message = f"plans={len(plans):,}"

        with FullProfileReporter(state, layout=str(args.progress_layout)) as reporter:
            block_counter = 0
            for job in iter_profile_jobs(stream_config, plans, max_blocks=int(args.max_blocks), mode=str(args.block_sampling)):
                block_counter += 1
                state.last_message = f"{job.plan.month} {job.plan.ticker} block={job.block_id}"
                block_rows = profile_block(
                    args=args,
                    stream_config=stream_config,
                    context_client_opts=context_client_opts,
                    rolling_config=rolling_config,
                    ctx_args=ctx_args,
                    job=job,
                    stream_queries=stream_queries,
                    shared_cache=shared_cache,
                )
                append_jsonl(report_path, {"event": "block", "block": block_counter, "ticker": job.plan.ticker, "month": job.plan.month, "job": job_to_dict(job), "steps": block_rows})
                update_state_from_block(state, block_rows)
                reporter.refresh()
                print(
                    json.dumps(
                        {
                            "block": block_counter,
                            "ticker": job.plan.ticker,
                            "steps": len(block_rows),
                            "rows": sum(int(row.get("rows") or 0) for row in block_rows),
                            "seconds": sum(float(row.get("seconds") or 0.0) for row in block_rows),
                            "errors": sum(1 for row in block_rows if row.get("status") != "ok"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    except KeyboardInterrupt:
        state.last_message = "interrupt received; cancelling ClickHouse queries"
        cancel_stream_queries(stream_config, stream_queries)
        cancel_context_queries(client_opts=context_client_opts, reason="full_modality_profile_interrupt")
        append_jsonl(report_path, {"event": "interrupted", "state": state_summary(state)})
        raise
    finally:
        cancel_stream_queries(stream_config, stream_queries)
        cancel_context_queries(client_opts=context_client_opts, reason="full_modality_profile_shutdown")

    summary = state_summary(state)
    summary["shared_cache"] = shared_cache.summary()
    append_jsonl(report_path, {"event": "summary", **summary})
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 1 if int(state.errors) and bool(args.strict_modalities) else 0


def profile_block(
    *,
    args: argparse.Namespace,
    stream_config: ClickHouseTickerStreamConfig,
    context_client_opts: Mapping[str, str],
    rolling_config: RollingMarketDataConfig,
    ctx_args: argparse.Namespace,
    job: Any,
    stream_queries: ActiveQueryRegistry,
    shared_cache: FullProfileSharedCache,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events_holder: dict[str, Any] = {}

    def fetch_events() -> Any:
        frame = fetch_event_frame(stream_config, job, active_queries=stream_queries)
        if "event_ticker" in frame.columns and "ticker" not in frame.columns:
            frame = frame.rename({"event_ticker": "ticker"})
        return frame

    event_result, events = run_step("events.fetch", fetch_events)
    rows.append(asdict(event_result))
    if event_result.status != "ok" or events is None:
        return rows
    events_holder["events"] = events
    window = month_window(job.plan.month)
    origin_local_dates = local_dates_from_origin_events(
        events,
        origin_start_ordinal=int(job.origin_start_ordinal),
        origin_end_ordinal=int(job.origin_end_ordinal),
    )

    block_result, _block = run_step("events.packed_block_and_labels", lambda: build_packed_block_from_events(stream_config, job, events, worker_id=0))
    rows.append(asdict(block_result))
    intraday_result, intraday = run_step("intraday.base_bars", lambda: _build_intraday_base_bars(events_holder["events"]))
    rows.append(asdict(intraday_result))
    condition_result, condition_events = run_step("intraday.condition_events", lambda: _build_intraday_condition_events(events_holder["events"]))
    rows.append(asdict(condition_result))

    context_tasks: dict[str, Callable[[], Any]] = {
        "context.ticker_news_embeddings": lambda: _query_ticker_news(ctx_args, context_client_opts, rolling_config, window, job.plan.ticker),
        "context.sec_embeddings": lambda: _query_sec_tokens(ctx_args, context_client_opts, rolling_config, window, job.plan.ticker),
        "context.xbrl": lambda: _query_xbrl(ctx_args, context_client_opts, rolling_config, window, job.plan.ticker),
        "context.corporate_actions": lambda: _query_corporate_actions(ctx_args, context_client_opts, rolling_config, window, job.plan.ticker),
        "bars.ticker_daily": lambda: _query_daily_bars(ctx_args, context_client_opts, rolling_config, window, symbols=(job.plan.ticker,)),
    }
    cached_context_tasks: dict[str, tuple[tuple[str, ...], Callable[[], Any]]] = {
        "context.market_news_embeddings": (("market_news", job.plan.month), lambda: _query_market_news(ctx_args, context_client_opts, rolling_config, window)),
        "bars.global_daily": (("global_daily", job.plan.month), lambda: _query_daily_bars(ctx_args, context_client_opts, rolling_config, window, symbols=tuple(rolling_config.global_symbols))),
        "scanner.cache": (("scanner", job.plan.month, "|".join(origin_local_dates)), lambda: load_scanner_frames(Path(args.scanner_cache_root), job.plan.month, origin_local_dates, require=bool(args.require_scanner))),
    }
    with ThreadPoolExecutor(max_workers=max(1, int(args.context_workers)), thread_name_prefix="full-modality-context") as pool:
        futures = {pool.submit(run_step, name, task): name for name, task in context_tasks.items()}
        futures.update({pool.submit(run_cached_step, name, shared_cache, key, task): name for name, (key, task) in cached_context_tasks.items()})
        for future in as_completed(futures):
            result, _payload = future.result()
            rows.append(asdict(result))
    return rows


def iter_profile_jobs(
    config: ClickHouseTickerStreamConfig,
    plans: list[Any],
    *,
    max_blocks: int,
    mode: str,
) -> Iterable[Any]:
    emitted = 0
    limit = max(0, int(max_blocks))
    if mode == "sequential":
        for plan in plans:
            for job in build_block_jobs(config, plan):
                if limit and emitted >= limit:
                    return
                emitted += 1
                yield job
        return

    active = [iter(build_block_jobs(config, plan)) for plan in plans]
    while active:
        next_active = []
        for iterator in active:
            if limit and emitted >= limit:
                return
            try:
                job = next(iterator)
            except StopIteration:
                continue
            emitted += 1
            yield job
            next_active.append(iterator)
        active = next_active


def run_step(name: str, fn: Callable[[], Any]) -> tuple[StepResult, Any | None]:
    started = time.perf_counter()
    try:
        payload = fn()
        seconds = time.perf_counter() - started
        rows, columns, bytes_used, detail = summarize_payload(payload)
        return StepResult(name=name, status="ok", seconds=seconds, rows=rows, columns=columns, bytes=bytes_used, detail=detail), payload
    except Exception as exc:  # noqa: BLE001
        return StepResult(name=name, status="error", seconds=time.perf_counter() - started, error=repr(exc), detail={"traceback": traceback.format_exc(limit=8)}), None


def run_cached_step(name: str, cache: FullProfileSharedCache, key: tuple[str, ...], fn: Callable[[], Any]) -> tuple[StepResult, Any | None]:
    started = time.perf_counter()
    record = cache.get(key)
    if record is not None:
        detail = {"cache_hit": True, "cached_bytes": int(record.bytes), "cache_key": "|".join(key)}
        if "columns_preview" in record.detail:
            detail["columns_preview"] = record.detail["columns_preview"]
        return StepResult(name=name, status="ok", seconds=time.perf_counter() - started, rows=int(record.rows), columns=int(record.columns), bytes=0, detail=detail), record.payload

    result, payload = run_step(name, fn)
    result.detail = dict(result.detail)
    result.detail["cache_hit"] = False
    result.detail["cache_key"] = "|".join(key)
    if result.status == "ok" and payload is not None:
        cache.put(
            key,
            CachedPayloadRecord(
                payload=payload,
                rows=int(result.rows),
                columns=int(result.columns),
                bytes=int(result.bytes),
                detail=dict(result.detail),
            ),
        )
    return result, payload


def summarize_payload(payload: Any) -> tuple[int, int, int, dict[str, Any]]:
    if payload is None:
        return 0, 0, 0, {}
    if isinstance(payload, Mapping):
        rows = 0
        columns = 0
        bytes_used = 0
        detail: dict[str, Any] = {}
        for key, value in payload.items():
            r, c, b, d = summarize_payload(value)
            rows += r
            columns += c
            bytes_used += b
            detail[str(key)] = {"rows": r, "columns": c, "bytes": b, **d}
        return rows, columns, bytes_used, detail
    if hasattr(payload, "height") and hasattr(payload, "columns"):
        rows = int(payload.height)
        columns = len(payload.columns)
        bytes_used = estimate_payload_bytes(payload)
        return rows, columns, bytes_used, {"columns_preview": list(payload.columns[:24])}
    if hasattr(payload, "origin_count") and hasattr(payload, "event_count"):
        bytes_used = int(getattr(payload, "events").nbytes)
        bytes_used += sum(int(value.nbytes) for value in getattr(payload, "labels", {}).values())
        return int(payload.origin_count), int(getattr(payload, "events").shape[1]), bytes_used, {"event_count": int(payload.event_count), "label_count": len(getattr(payload, "labels", {}))}
    return 1, 0, 0, {"type": type(payload).__name__}


def estimate_payload_bytes(frame: Any) -> int:
    try:
        return int(frame.estimated_size())
    except Exception:
        return int(getattr(frame, "height", 0) * max(1, len(getattr(frame, "columns", ()))) * 8)


def local_dates_from_origin_events(events: Any, *, origin_start_ordinal: int, origin_end_ordinal: int) -> tuple[str, ...]:
    if "local_date" not in events.columns or events.height == 0:
        return ()
    import polars as pl

    origin_events = events.filter((pl.col("ordinal") >= int(origin_start_ordinal)) & (pl.col("ordinal") <= int(origin_end_ordinal)))
    if origin_events.height == 0:
        return ()
    values = origin_events.select("local_date").unique().sort("local_date").to_series().to_list()
    return tuple(str(value)[:10] for value in values)


def load_scanner_frames(cache_root: Path, month: str, local_dates: tuple[str, ...], *, require: bool) -> dict[str, Any]:
    import polars as pl

    frames: dict[str, Any] = {}
    missing: list[str] = []
    root = Path(cache_root)
    month_dir = root / f"month={month}"
    if not month_dir.exists() and root.name == f"month={month}":
        month_dir = root
    for date_text in local_dates:
        path = month_dir / "global" / "scanner" / f"scanner_{date_text}.parquet"
        if path.exists():
            frames[date_text] = pl.read_parquet(path)
        else:
            missing.append(str(path))
    if missing and require:
        raise RuntimeError(f"Missing scanner cache files: {missing[:5]}")
    if missing:
        frames["_missing"] = pl.DataFrame({"path": missing})
    return frames


def update_state_from_block(state: ProfileState, rows: list[dict[str, Any]]) -> None:
    state.blocks_done += 1
    state.steps_done += len(rows)
    state.errors += sum(1 for row in rows if row.get("status") != "ok")
    state.last_steps = rows[-12:]
    if rows:
        state.last_message = rows[-1].get("name", "-")


def state_summary(state: ProfileState) -> dict[str, Any]:
    return {
        "run_dir": str(state.run_dir),
        "blocks_done": int(state.blocks_done),
        "steps_done": int(state.steps_done),
        "errors": int(state.errors),
        "elapsed_seconds": float(state.elapsed()),
        "last_message": state.last_message,
    }


def job_to_dict(job: Any) -> dict[str, Any]:
    return {
        "ticker": job.plan.ticker,
        "month": job.plan.month,
        "block_id": int(job.block_id),
        "origin_start_ordinal": int(job.origin_start_ordinal),
        "origin_end_ordinal": int(job.origin_end_ordinal),
        "fetch_start_ordinal": int(job.fetch_start_ordinal),
        "fetch_end_ordinal": int(job.fetch_end_ordinal),
    }


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), default=str, sort_keys=True) + "\n")


class FullProfileReporter:
    def __init__(self, state: ProfileState, *, layout: str) -> None:
        self.state = state
        self.layout = str(layout)
        self._live: Any | None = None

    def __enter__(self) -> "FullProfileReporter":
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._live = Live(self.render(), console=Console(), refresh_per_second=2, screen=False, transient=False, auto_refresh=False)
                self._live.start(refresh=True)
            except Exception:
                if self.layout == "rich":
                    raise
                self._live = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self.refresh()
            self._live.stop()

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.render(), refresh=True)

    def render(self) -> Any:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table

        summary = Table(box=box.SIMPLE, expand=True, show_edge=False)
        summary.add_column("Metric", style="cyan", no_wrap=True)
        summary.add_column("Value", no_wrap=True)
        summary.add_column("Detail")
        summary.add_row("Run", str(self.state.run_dir), "")
        summary.add_row("Blocks", f"{self.state.blocks_done:,}", f"steps={self.state.steps_done:,} errors={self.state.errors:,}")
        summary.add_row("Elapsed", f"{self.state.elapsed():.1f}s", self.state.last_message)

        steps = Table(box=box.SIMPLE, expand=True)
        steps.add_column("Step", style="cyan")
        steps.add_column("Status", no_wrap=True)
        steps.add_column("Rows", justify="right", no_wrap=True)
        steps.add_column("MiB", justify="right", no_wrap=True)
        steps.add_column("Sec", justify="right", no_wrap=True)
        steps.add_column("Detail", overflow="ellipsis")
        for row in self.state.last_steps[-12:]:
            steps.add_row(
                str(row.get("name") or "-"),
                str(row.get("status") or "-"),
                f"{int(row.get('rows') or 0):,}",
                f"{int(row.get('bytes') or 0) / (1024 * 1024):,.1f}",
                f"{float(row.get('seconds') or 0.0):.3f}",
                str(row.get("error") or ""),
            )
        return Group(Panel(summary, title="Full Modality Loader Profile", border_style="cyan"), Panel(steps, title="Recent Steps", border_style="green"))


if __name__ == "__main__":
    raise SystemExit(main())
