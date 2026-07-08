from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.daily_index_dataset import (
    BAR_FAMILY_FEATURE_KEYS,
    BAR_FAMILY_KEYS,
    DEFAULT_SCANNER_GROUPS,
    DEFAULT_SCANNER_HORIZONS,
    _intraday_label_resolution_us,
    _scanner_column_token,
    _duration_us,
)


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache")
SESSION_START_US = 4 * 60 * 60 * 1_000_000


@dataclass(slots=True)
class ScannerWorkerSlot:
    worker_id: int
    status: str = "idle"
    source_date: str = "-"
    stage: str = "-"
    current: str = "-"
    completed: int = 0
    total: int = 0
    rows: int = 0
    bytes_written: int = 0
    rate: float = 0.0
    started_at: float = 0.0


class ScannerBuildState:
    def __init__(self, *, cache_root: Path, month: str, total_days: int, total_files: int, workers: int, emit_text: bool = False) -> None:
        self.cache_root = Path(cache_root)
        self.month = str(month)
        self.total_days = int(total_days)
        self.total_files = int(total_files)
        self.done_days = 0
        self.done_files = 0
        self.rows = 0
        self.bytes_written = 0
        self.status = "starting"
        self.started_at = time.perf_counter()
        self.messages: deque[str] = deque(maxlen=12)
        self.errors: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.workers = [ScannerWorkerSlot(worker_id=index) for index in range(max(1, int(workers)))]
        self.emit_text = bool(emit_text)
        self._lock = threading.Lock()

    def message(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        with self._lock:
            self.messages.append(line)
        if self.emit_text:
            print(line, flush=True)

    def add_result(self, result: Mapping[str, Any]) -> None:
        with self._lock:
            self.results.append(dict(result))
            self.done_days += 1
            self.done_files += int(result.get("files") or 0)
            self.rows += int(result.get("rows") or 0)
            self.bytes_written += int(result.get("bytes") or 0)

    def add_error(self, *, worker: int, source_date: str, error: BaseException) -> None:
        with self._lock:
            self.errors.append(
                {
                    "worker": int(worker),
                    "source_date": str(source_date),
                    "error": repr(error),
                    "traceback": traceback.format_exc(),
                }
            )
            self.status = "error"


class ScannerDashboard:
    def __init__(self, state: ScannerBuildState, *, refresh_per_second: float, progress_screen: bool, progress_layout: str) -> None:
        self.state = state
        self.refresh_per_second = max(0.5, float(refresh_per_second))
        self.progress_screen = bool(progress_screen)
        self.progress_layout = str(progress_layout)
        self._live = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def __enter__(self) -> "ScannerDashboard":
        if self.progress_layout != "text":
            try:
                from rich.live import Live

                self._live = Live(
                    self._render(),
                    refresh_per_second=self.refresh_per_second,
                    transient=False,
                    auto_refresh=False,
                    screen=self.progress_screen,
                    vertical_overflow="crop",
                )
                self._live.start(refresh=True)
                self._thread = threading.Thread(target=self._refresh_loop, name="scanner-dashboard", daemon=True)
                self._thread.start()
            except Exception as exc:  # noqa: BLE001
                self._live = None
                print(f"Rich progress unavailable; falling back to text progress: {exc!r}", flush=True)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._live is not None:
            self.refresh()
            self._live.stop()

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _refresh_loop(self) -> None:
        interval = 1.0 / self.refresh_per_second
        while not self._stop.wait(interval):
            self.refresh()

    def _render(self) -> object:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.progress_bar import ProgressBar
        from rich.rule import Rule
        from rich.table import Table

        with self.state._lock:
            workers = [replace(worker) for worker in self.state.workers]
            messages = list(self.state.messages)
            errors = list(self.state.errors)
            status = self.state.status
            done_days = self.state.done_days
            done_files = self.state.done_files
            rows = self.state.rows
            bytes_written = self.state.bytes_written
        elapsed = max(0.001, time.perf_counter() - self.state.started_at)
        day_rate = done_days / elapsed * 60.0
        file_rate = done_files / elapsed * 60.0
        remaining_days = max(0, self.state.total_days - done_days)
        eta = remaining_days / (done_days / elapsed) if done_days > 0 else 0.0

        summary = Table(box=box.SIMPLE, expand=True, show_edge=False)
        summary.add_column("Metric", style="cyan", no_wrap=True)
        summary.add_column("Value", no_wrap=True)
        summary.add_column("Detail")
        summary.add_row("Status", status.upper(), f"errors={len(errors):,}")
        summary.add_row("Cache", str(self.state.cache_root), f"month={self.state.month}")
        summary.add_row("Days", f"{done_days:,}/{self.state.total_days:,}", f"{day_rate:.2f} day/min eta={format_seconds(eta)}")
        summary.add_row("Files", f"{done_files:,}/{self.state.total_files:,}", f"{file_rate:.2f} file/min")
        summary.add_row("Rows", f"{rows:,}", f"bytes={format_bytes(bytes_written)} elapsed={format_seconds(elapsed)}")

        progress = Progress(
            TextColumn("[cyan]Overall", justify="left"),
            BarColumn(bar_width=None),
            TextColumn(f"{done_days:,}/{self.state.total_days:,} days  eta={format_seconds(eta)}", justify="right"),
            expand=True,
        )
        progress.add_task("overall", total=max(1, self.state.total_days), completed=min(done_days, self.state.total_days))

        worker_table = Table(box=box.SIMPLE, expand=True)
        worker_table.add_column("W", justify="right", no_wrap=True)
        worker_table.add_column("Status", no_wrap=True)
        worker_table.add_column("Stage", no_wrap=True, style="cyan")
        worker_table.add_column("Day", no_wrap=True)
        worker_table.add_column("Bar", no_wrap=True)
        worker_table.add_column("Progress", no_wrap=True)
        worker_table.add_column("Rows", justify="right", no_wrap=True)
        worker_table.add_column("Bytes", justify="right", no_wrap=True)
        worker_table.add_column("Current", overflow="ellipsis", max_width=60)
        for worker in workers:
            worker_table.add_row(
                f"{worker.worker_id:02d}",
                worker.status,
                worker.stage,
                worker.source_date,
                ProgressBar(total=max(1, worker.total), completed=min(max(0, worker.completed), max(1, worker.total)), width=18),
                f"{worker.completed:,}/{max(worker.total, 1):,}",
                f"{worker.rows:,}",
                format_bytes(worker.bytes_written),
                worker.current,
            )

        messages_table = Table(box=box.SIMPLE, show_header=False, expand=True)
        messages_table.add_column("Message")
        for line in messages[-6:]:
            messages_table.add_row(str(line))
        if errors:
            for item in errors[-3:]:
                messages_table.add_row(f"ERROR worker={item['worker']} day={item['source_date']} {item['error']}")

        return Group(
            Panel(Group(summary, progress), title="Daily Scanner Cache", box=box.ROUNDED, border_style="red" if errors else "green", padding=(0, 1)),
            Panel(Group(Rule(style="dim blue"), worker_table), title="Workers", box=box.ROUNDED, border_style="blue", padding=(0, 1)),
            Panel(messages_table, title="Messages / Errors", box=box.ROUNDED, border_style="yellow", padding=(0, 1)),
        )


def format_seconds(seconds: float | int) -> str:
    value = max(0.0, float(seconds or 0.0))
    if value < 60.0:
        return f"{value:.1f}s"
    if value < 3600.0:
        return f"{value / 60.0:.1f}m"
    return f"{value / 3600.0:.1f}h"


def format_bytes(value: int | float) -> str:
    size = float(value or 0)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(size) >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size):,} {units[index]}"
    return f"{size:.1f} {units[index]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build daily scanner artifacts from an existing daily-index cache.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--month", default="", help="YYYY-MM month to process.")
    parser.add_argument("--source-date", default="", help="Optional YYYY-MM-DD day inside the month.")
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker subset for smoke tests.")
    parser.add_argument("--scanner-resolution-us", type=int, default=1_000_000)
    parser.add_argument("--horizons", default=",".join(DEFAULT_SCANNER_HORIZONS))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=1.0)
    parser.add_argument("--progress-screen", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(args)


def run(args: argparse.Namespace) -> int:
    if not args.month:
        raise SystemExit("--month is required")
    started = time.perf_counter()
    cache_root = Path(args.cache_root)
    month_dir = cache_root / f"month={args.month}"
    if not month_dir.exists():
        raise FileNotFoundError(f"Missing cache month directory: {month_dir}")
    jobs = discover_day_jobs(month_dir=month_dir, source_date=str(args.source_date), tickers=_split_csv(args.tickers))
    if not jobs:
        raise RuntimeError(f"No intraday_base_bars files found for month={args.month} source_date={args.source_date or '*'}")
    grouped: dict[str, list[Path]] = {}
    for source_date, path in jobs:
        grouped.setdefault(source_date, []).append(path)
    state = ScannerBuildState(
        cache_root=cache_root,
        month=str(args.month),
        total_days=len(grouped),
        total_files=len(jobs),
        workers=int(args.workers),
        emit_text=str(args.progress_layout) == "text",
    )
    stop_event = threading.Event()
    work_queue: queue.Queue[tuple[str, list[Path]] | None] = queue.Queue()
    for source_date, files in sorted(grouped.items()):
        work_queue.put((source_date, files))
    for _ in state.workers:
        work_queue.put(None)
    state.message(f"planned month={args.month} days={len(grouped):,} files={len(jobs):,} cache={cache_root}")
    threads: list[threading.Thread] = []
    try:
        with ScannerDashboard(state, refresh_per_second=args.progress_refresh_per_second, progress_screen=args.progress_screen, progress_layout=args.progress_layout):
            state.status = "running"
            for slot in state.workers:
                thread = threading.Thread(target=scanner_worker, name=f"scanner-{slot.worker_id:02d}", args=(slot, args, month_dir, work_queue, state, stop_event))
                thread.start()
                threads.append(thread)
            while any(thread.is_alive() for thread in threads):
                if state.errors:
                    stop_event.set()
                time.sleep(0.25)
            for thread in threads:
                thread.join(timeout=2.0)
    except KeyboardInterrupt:
        state.status = "interrupted"
        state.message("interrupt received; stopping scanner workers")
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2.0)
        return 130
    if state.errors:
        raise RuntimeError(f"Scanner cache failed with {len(state.errors):,} error(s): {state.errors[-1]['error']}")
    results = list(state.results)
    manifest_path = month_dir / "global" / "scanner" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "cache_version": "daily_index_scanner_cache_v1",
                "month": args.month,
                "scanner_resolution_us": int(args.scanner_resolution_us),
                "horizons": _split_csv(args.horizons),
                "top_k": int(args.top_k),
                "days": sorted(results, key=lambda row: row["source_date"]),
                "seconds": time.perf_counter() - started,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    state.status = "complete"
    state.message(f"complete days={len(results):,} rows={sum(int(r['rows']) for r in results):,} seconds={time.perf_counter() - started:.1f}")
    return 0


def scanner_worker(
    slot: ScannerWorkerSlot,
    args: argparse.Namespace,
    month_dir: Path,
    work_queue: "queue.Queue[tuple[str, list[Path]] | None]",
    state: ScannerBuildState,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            item = work_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if item is None:
                slot.status = "idle"
                slot.stage = "-"
                slot.current = "-"
                slot.source_date = "-"
                return
            source_date, files = item
            slot.status = "running"
            slot.source_date = str(source_date)
            slot.started_at = time.perf_counter()
            slot.total = max(1, len(files))
            slot.completed = 0
            slot.rows = 0
            slot.bytes_written = 0
            result = build_day_scanner(args=args, month_dir=month_dir, source_date=source_date, files=files, slot=slot)
            state.add_result(result)
            slot.status = "done"
            slot.stage = "done"
            slot.completed = slot.total
            slot.current = str(result["path"])
            state.message(f"day {source_date} rows={int(result['rows']):,} files={int(result['files']):,} bytes={int(result['bytes']):,} seconds={float(result['seconds']):.1f}")
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=slot.worker_id, source_date=getattr(slot, "source_date", ""), error=exc)
            stop_event.set()
        finally:
            work_queue.task_done()


def discover_day_jobs(*, month_dir: Path, source_date: str, tickers: tuple[str, ...]) -> list[tuple[str, Path]]:
    selected_tickers = {ticker.upper() for ticker in tickers}
    out: list[tuple[str, Path]] = []
    for manifest_path in sorted(month_dir.glob("ticker=*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ticker = str(manifest.get("ticker") or "").upper()
        if selected_tickers and ticker not in selected_tickers:
            continue
        package_dir = manifest_path.parent
        for part in manifest.get("modality_parts") or ():
            paths = dict(part.get("output_paths") or {})
            path_text = paths.get("intraday_base_bars")
            day = _source_date_from_part(part)
            if not path_text or not day:
                continue
            if source_date and day != source_date[:10]:
                continue
            path = Path(str(path_text))
            if not path.is_absolute():
                path = package_dir / path
            if path.exists():
                out.append((day, path))
    return out


def build_day_scanner(
    *,
    args: argparse.Namespace,
    month_dir: Path,
    source_date: str,
    files: list[Path],
    slot: ScannerWorkerSlot | None = None,
) -> dict[str, Any]:
    import polars as pl

    started = time.perf_counter()
    frames: list[Any] = []
    read_started = time.perf_counter()
    for index, path in enumerate(files, start=1):
        if slot is not None:
            slot.stage = "read"
            slot.current = path.name
            slot.completed = index - 1
            slot.total = max(1, len(files))
        frame = pl.read_parquet(path)
        frames.append(frame)
        if slot is not None:
            slot.completed = index
            slot.rows += int(frame.height)
    base = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
    if slot is not None:
        slot.stage = "process"
        slot.current = f"concat {len(frames):,} files in {format_seconds(time.perf_counter() - read_started)}"
        slot.completed = 0
        slot.total = max(1, int(base.height))
    if base.height <= 0:
        frame = pl.DataFrame()
    else:
        frame = build_scanner_frame(
            base=base,
            source_date=source_date,
            scanner_resolution_us=int(args.scanner_resolution_us),
            horizons=_split_csv(args.horizons),
            top_k=int(args.top_k),
        )
    if slot is not None:
        slot.completed = int(base.height)
        slot.rows = int(frame.height)
        slot.stage = "write"
        slot.current = f"scanner_{source_date}.parquet"
    output = month_dir / "global" / "scanner" / f"scanner_{source_date}.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not bool(args.overwrite):
        raise FileExistsError(f"Scanner artifact already exists: {output}. Pass --overwrite to replace it.")
    tmp = output.with_name(f"{output.name}.{time.time_ns()}.tmp")
    frame.write_parquet(tmp, compression="zstd")
    tmp.replace(output)
    if slot is not None:
        slot.bytes_written = int(output.stat().st_size)
        slot.rate = int(frame.height) / max(0.001, time.perf_counter() - started)
    return {
        "source_date": source_date,
        "files": len(files),
        "rows": int(frame.height),
        "bytes": int(output.stat().st_size),
        "seconds": time.perf_counter() - started,
        "path": str(output),
    }


def build_scanner_frame(*, base: Any, source_date: str, scanner_resolution_us: int, horizons: tuple[str, ...], top_k: int) -> Any:
    import polars as pl

    scanner_resolution_us = max(1, int(scanner_resolution_us))
    trade = (
        base.filter((pl.col("local_date").cast(pl.Utf8).str.slice(0, 10) == source_date[:10]) & (pl.col("bar_family") == "trade") & (pl.col("label_resolution_us") == scanner_resolution_us))
        .sort(["ticker", "bucket_index"])
        .with_columns(
            [
                pl.col("open").first().over("ticker").alias("_day_open"),
                pl.col("bucket_index").cast(pl.Int64).alias("scanner_bucket"),
                pl.col("last_event_timestamp_us").cast(pl.Int64).alias("scanner_timestamp_us"),
                pl.lit(scanner_resolution_us).cast(pl.Int64).alias("scanner_resolution_us"),
                pl.lit(source_date[:10]).alias("source_date"),
            ]
        )
        .with_columns(
            [
                pl.when(pl.col("_day_open") > 0).then((pl.col("close") / pl.col("_day_open")) - 1.0).otherwise(0.0).cast(pl.Float32).alias("_change_score"),
                pl.col("size_sum").fill_null(0).cast(pl.Float32).alias("_volume_score"),
            ]
        )
        .select(["source_date", "ticker", "ticker_id", "scanner_bucket", "scanner_timestamp_us", "scanner_resolution_us", "close", "_change_score", "_volume_score"])
    )
    out = trade
    for group_name in DEFAULT_SCANNER_GROUPS:
        if group_name == "top_gainers":
            ranked_source = trade.with_columns(pl.col("_change_score").alias("_rank_score"))
        elif group_name == "top_volume_penny":
            ranked_source = trade.filter(pl.col("close") < 1.0).with_columns(pl.col("_volume_score").alias("_rank_score"))
        else:
            ranked_source = trade.filter(pl.col("close") >= 1.0).with_columns(pl.col("_volume_score").alias("_rank_score"))
        ranked = (
            ranked_source.sort(["scanner_bucket", "_rank_score", "ticker"], descending=[False, True, False])
            .with_columns((pl.col("ticker").cum_count().over("scanner_bucket") - 1).cast(pl.Int32).alias(f"{group_name}_rank"))
            .with_columns(
                [
                    pl.col("_rank_score").cast(pl.Float32).alias(f"{group_name}_score"),
                    pl.when(pl.max(f"{group_name}_rank").over("scanner_bucket") > 0)
                    .then(1.0 - (pl.col(f"{group_name}_rank") / pl.max(f"{group_name}_rank").over("scanner_bucket")))
                    .otherwise(1.0)
                    .cast(pl.Float32)
                    .alias(f"{group_name}_percentile"),
                ]
            )
            .select(["ticker", "scanner_bucket", f"{group_name}_rank", f"{group_name}_score", f"{group_name}_percentile"])
        )
        out = out.join(ranked, on=["ticker", "scanner_bucket"], how="left")
    rank_columns = [column for column in out.columns if column.endswith("_rank")]
    for column in rank_columns:
        out = out.with_columns(pl.col(column).fill_null(-1).cast(pl.Int32))
    for column in [column for column in out.columns if column.endswith("_score") or column.endswith("_percentile")]:
        out = out.with_columns(pl.col(column).fill_null(0.0).cast(pl.Float32))
    for horizon in horizons:
        out = add_horizon_columns(out=out, base=base, source_date=source_date, horizon=horizon, scanner_resolution_us=scanner_resolution_us)
    return out.drop(["close", "_change_score", "_volume_score"])


def add_horizon_columns(*, out: Any, base: Any, source_date: str, horizon: str, scanner_resolution_us: int) -> Any:
    import polars as pl

    horizon_us = _duration_us(horizon)
    resolution_us = _intraday_label_resolution_us(horizon, horizon_us)
    token = _scanner_column_token(horizon)
    scanner_end_us = (pl.col("scanner_bucket").cast(pl.Int64) + 1) * int(scanner_resolution_us)
    join_bucket_expr = ((scanner_end_us // int(resolution_us)) - 1).clip(lower_bound=int(SESSION_START_US // int(resolution_us))).alias("_join_bucket")
    out = out.with_columns(join_bucket_expr)
    for family in BAR_FAMILY_KEYS:
        include_timestamp = f"{token}_timestamp_us" not in out.columns
        select_columns = ["ticker", "_join_bucket", *BAR_FAMILY_FEATURE_KEYS[family]]
        if include_timestamp:
            select_columns.insert(2, "last_event_timestamp_us")
        source = (
            base.filter(
                (pl.col("local_date").cast(pl.Utf8).str.slice(0, 10) == source_date[:10])
                & (pl.col("bar_family") == family)
                & (pl.col("label_resolution_us") == int(resolution_us))
            )
            .rename({"bucket_index": "_join_bucket"})
            .select(select_columns)
        )
        renamed = {name: f"{family}_{token}_{name}" for name in BAR_FAMILY_FEATURE_KEYS[family]}
        rename_map = dict(renamed)
        if include_timestamp:
            rename_map["last_event_timestamp_us"] = f"{token}_timestamp_us"
        source = source.rename(rename_map)
        out = out.join(source, on=["ticker", "_join_bucket"], how="left")
        out = out.with_columns(pl.col(f"{family}_{token}_open").is_not_null().alias(f"{family}_{token}_available"))
        for feature in BAR_FAMILY_FEATURE_KEYS[family]:
            out = out.with_columns(pl.col(f"{family}_{token}_{feature}").fill_null(0.0).cast(pl.Float32))
    if f"{token}_timestamp_us" in out.columns:
        out = out.with_columns(pl.col(f"{token}_timestamp_us").fill_null(pl.col("scanner_timestamp_us")).cast(pl.Int64))
    return out.drop("_join_bucket")


def _source_date_from_part(part: Mapping[str, Any]) -> str:
    raw = str(part.get("source_date") or "")
    if raw:
        return raw[:10]
    job_id = str(part.get("job_id") or "")
    for token in job_id.split("|"):
        if len(token) >= 10 and token[4:5] == "-" and token[7:8] == "-":
            return token[:10]
    return ""


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


if __name__ == "__main__":
    raise SystemExit(main())
