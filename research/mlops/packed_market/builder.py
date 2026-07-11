from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import polars as pl

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.packed_market.cache import (
    PACKED_CACHE_FORMAT,
    PACKED_CACHE_SCHEMA_VERSION,
    PackedBlockManifest,
    PackedCacheManifest,
    append_jsonl,
    choose_first_column,
    numeric_columns,
    read_json,
    symbol_to_ticker_dir,
    ticker_dir_to_symbol,
    utc_now_iso,
    write_json,
)


IDENTITY_COLUMNS = {
    "ordinal",
    "event_ordinal",
    "timestamp_us",
    "sip_timestamp_us",
    "origin_ordinal",
    "origin_timestamp_us",
    "origin_event_index",
    "origin_position",
    "event_index",
    "source_date",
}


@dataclass(slots=True)
class BuilderStats:
    packages_total: int = 0
    packages_done: int = 0
    packages_failed: int = 0
    blocks_written: int = 0
    event_rows: int = 0
    origin_rows: int = 0
    started: float = field(default_factory=time.perf_counter)
    messages: list[str] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def message(self, text: str) -> None:
        with self.lock:
            self.messages.append(f"{time.strftime('%H:%M:%S')} {text}")
            del self.messages[:-8]


@dataclass(frozen=True, slots=True)
class PackageJob:
    month: str
    source_package_dir: Path
    output_package_dir: Path
    ticker: str
    ticker_dir_name: str


@dataclass(frozen=True, slots=True)
class PackageResult:
    job: PackageJob
    blocks: int
    event_rows: int
    origin_rows: int
    seconds: float
    status: str
    error: str = ""


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build packed market block cache from daily-index streaming cache.")
    parser.add_argument("--source-cache-root", default=r"D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02")
    parser.add_argument("--output-root", default=r"D:\market-data\prepared\packed_market_block_cache")
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--months", default="", help="Comma-separated YYYY-MM months. Empty means all months discovered in source cache.")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker symbols for smoke tests. Empty means all.")
    parser.add_argument("--max-packages", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-origins-per-block", type=int, default=65_536)
    parser.add_argument("--max-events-per-block", type=int, default=4_000_000, help="Maximum event slice length consumed by one model block.")
    parser.add_argument("--context-events", type=int, default=1_024, help="Events kept before the first origin in a block. Uses 1023 prior plus current origin by default.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    source_root = Path(args.source_cache_root)
    cache_id = args.cache_id.strip() or f"packed_{source_root.name}"
    output_root = Path(args.output_root) / cache_id
    if output_root.exists() and not args.overwrite:
        raise RuntimeError(f"Output cache already exists; pass --overwrite to replace: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    stats = BuilderStats()
    report_path = output_root / "build_log.jsonl"
    errors_path = output_root / "errors.jsonl"
    jobs = discover_jobs(source_root, output_root, months=_csv(args.months), tickers=_csv(args.tickers))
    if int(args.max_packages) > 0:
        jobs = jobs[: int(args.max_packages)]
    stats.packages_total = len(jobs)
    append_jsonl(report_path, {"event": "start", "cache_id": cache_id, "source_cache_root": str(source_root), "output_root": str(output_root), "jobs": len(jobs), "args": vars(args)})
    stats.message(f"Discovered {len(jobs):,} ticker/month packages.")
    with PackedBuilderReporter(stats, output_root=output_root, layout=args.progress_layout) as reporter:
        reporter.refresh(force=True)
        if not jobs:
            raise RuntimeError(f"No ticker/month packages found in {source_root}")
        with futures.ThreadPoolExecutor(max_workers=max(1, int(args.workers)), thread_name_prefix="packed-builder") as pool:
            submitted = [pool.submit(build_package, args, job) for job in jobs]
            for future in futures.as_completed(submitted):
                result = future.result()
                with stats.lock:
                    if result.status == "ok":
                        stats.packages_done += 1
                        stats.blocks_written += int(result.blocks)
                        stats.event_rows += int(result.event_rows)
                        stats.origin_rows += int(result.origin_rows)
                    else:
                        stats.packages_failed += 1
                    stats.message(f"{result.job.month} {result.job.ticker}: {result.status} blocks={result.blocks:,} origins={result.origin_rows:,} {result.seconds:.1f}s")
                append_jsonl(report_path, {"event": "package", **result.__dict__, "job": job_to_dict(result.job)})
                if result.status != "ok":
                    append_jsonl(errors_path, {"event": "package_error", "job": job_to_dict(result.job), "error": result.error})
                reporter.refresh(force=True)
        manifest = PackedCacheManifest(
            format=PACKED_CACHE_FORMAT,
            schema_version=PACKED_CACHE_SCHEMA_VERSION,
            cache_id=cache_id,
            source_cache_root=str(source_root),
            months=tuple(sorted({job.month for job in jobs})),
            block_count=int(stats.blocks_written),
            event_rows=int(stats.event_rows),
            origin_rows=int(stats.origin_rows),
            created_at_utc=utc_now_iso(),
            builder={
                "max_origins_per_block": int(args.max_origins_per_block),
                "max_events_per_block": int(args.max_events_per_block),
                "context_events": int(args.context_events),
                "workers": int(args.workers),
            },
        )
        write_json(output_root / "manifest.json", manifest.to_dict())
        append_jsonl(report_path, {"event": "complete", **manifest.to_dict(), "failed": int(stats.packages_failed)})
        reporter.refresh(force=True)
    if stats.packages_failed:
        raise RuntimeError(f"Packed cache build finished with {stats.packages_failed:,} failed package(s). See {errors_path}")
    print(f"PACKED CACHE READY {output_root}", flush=True)
    return 0


def discover_jobs(source_root: Path, output_root: Path, *, months: tuple[str, ...], tickers: tuple[str, ...]) -> list[PackageJob]:
    selected_months = set(months)
    selected_tickers = {ticker.upper() for ticker in tickers}
    jobs: list[PackageJob] = []
    for month_dir in sorted(source_root.glob("month=*")):
        if not month_dir.is_dir():
            continue
        month = month_dir.name.split("=", 1)[1]
        if selected_months and month not in selected_months:
            continue
        for ticker_dir in sorted(month_dir.glob("ticker=*")):
            ticker_dir_name = ticker_dir.name.split("=", 1)[1]
            ticker = ticker_dir_to_symbol(ticker_dir_name).upper()
            if selected_tickers and ticker not in selected_tickers:
                continue
            jobs.append(
                PackageJob(
                    month=month,
                    source_package_dir=ticker_dir,
                    output_package_dir=output_root / f"month={month}" / f"ticker={symbol_to_ticker_dir(ticker)}",
                    ticker=ticker,
                    ticker_dir_name=ticker_dir_name,
                )
            )
    return jobs


def build_package(args: argparse.Namespace, job: PackageJob) -> PackageResult:
    started = time.perf_counter()
    try:
        if job.output_package_dir.exists() and bool(args.overwrite):
            _remove_tree(job.output_package_dir)
        job.output_package_dir.mkdir(parents=True, exist_ok=True)
        events = read_package_events(job.source_package_dir)
        origins = read_package_origins(job.source_package_dir)
        if events.is_empty() or origins.is_empty():
            return PackageResult(job, blocks=0, event_rows=events.height, origin_rows=0, seconds=time.perf_counter() - started, status="empty")
        event_ordinal_col = choose_first_column(set(events.columns), ("ordinal", "event_ordinal"))
        event_time_col = choose_first_column(set(events.columns), ("timestamp_us", "sip_timestamp_us"))
        origin_ordinal_col = choose_first_column(set(origins.columns), ("origin_ordinal", "ordinal"))
        origin_time_col = choose_first_column(set(origins.columns), ("origin_timestamp_us", "timestamp_us", "sip_timestamp_us"))
        if event_ordinal_col is None or event_time_col is None or origin_ordinal_col is None or origin_time_col is None:
            raise RuntimeError("events/origins are missing ordinal or timestamp identity columns")
        events = events.with_row_index("event_index").sort("event_index")
        origin_index = origins.join(
            events.select([pl.col(event_ordinal_col).alias(origin_ordinal_col), "event_index"]),
            on=origin_ordinal_col,
            how="inner",
        ).sort(["event_index", origin_time_col, origin_ordinal_col])
        origin_index = origin_index.rename({"event_index": "origin_event_index"})
        if origin_index.is_empty():
            return PackageResult(job, blocks=0, event_rows=events.height, origin_rows=0, seconds=time.perf_counter() - started, status="no_origin_match")
        labels = read_package_labels(job.source_package_dir)
        if labels is not None and not labels.is_empty():
            labels = _align_labels(labels, origin_index, origin_ordinal_col)
        event_feature_names = tuple(numeric_columns(events, exclude=IDENTITY_COLUMNS | {"event_index"}))
        events_path = job.output_package_dir / "events.parquet"
        origins_path = job.output_package_dir / "origins.parquet"
        labels_path = job.output_package_dir / "labels_intraday.parquet"
        events.write_parquet(events_path, compression="zstd", statistics=True)
        origin_index.write_parquet(origins_path, compression="zstd", statistics=True)
        if labels is not None and not labels.is_empty():
            labels.write_parquet(labels_path, compression="zstd", statistics=True)
            label_rel = str(labels_path.relative_to(job.output_package_dir.parents[1])).replace("\\", "/")
        else:
            label_rel = None
        package_manifest = {
            "month": job.month,
            "ticker": job.ticker,
            "event_rows": int(events.height),
            "origin_rows": int(origin_index.height),
            "event_feature_names": event_feature_names,
            "created_at_utc": utc_now_iso(),
            "source_package_dir": str(job.source_package_dir),
        }
        write_json(job.output_package_dir / "package_manifest.json", package_manifest)
        blocks = write_block_manifests(args, job, events, origin_index, event_feature_names, events_path, origins_path, label_rel)
        return PackageResult(job, blocks=blocks, event_rows=events.height, origin_rows=origin_index.height, seconds=time.perf_counter() - started, status="ok")
    except Exception as exc:  # noqa: BLE001
        return PackageResult(job, blocks=0, event_rows=0, origin_rows=0, seconds=time.perf_counter() - started, status="error", error=repr(exc) + "\n" + traceback.format_exc())


def read_package_events(package_dir: Path) -> pl.DataFrame:
    files = sorted((package_dir / "events").glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    frame = pl.concat([pl.read_parquet(path) for path in files], how="diagonal_relaxed")
    columns = set(frame.columns)
    ordinal = choose_first_column(columns, ("ordinal", "event_ordinal"))
    timestamp = choose_first_column(columns, ("timestamp_us", "sip_timestamp_us"))
    if ordinal is None or timestamp is None:
        raise RuntimeError(f"events parquet files in {package_dir} do not contain ordinal/timestamp columns")
    sort_cols = [timestamp, ordinal]
    frame = frame.sort(sort_cols).unique(subset=[ordinal], keep="first", maintain_order=True)
    return frame


def read_package_origins(package_dir: Path) -> pl.DataFrame:
    files = sorted((package_dir / "origins").glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    frame = pl.concat([pl.read_parquet(path) for path in files], how="diagonal_relaxed")
    columns = set(frame.columns)
    ordinal = choose_first_column(columns, ("origin_ordinal", "ordinal"))
    timestamp = choose_first_column(columns, ("origin_timestamp_us", "timestamp_us", "sip_timestamp_us"))
    if ordinal is None or timestamp is None:
        raise RuntimeError(f"origins parquet files in {package_dir} do not contain ordinal/timestamp columns")
    return frame.sort([timestamp, ordinal]).unique(subset=[ordinal], keep="first", maintain_order=True)


def read_package_labels(package_dir: Path) -> pl.DataFrame | None:
    label_dir = package_dir / "intraday_labels"
    files = sorted(label_dir.glob("*.parquet"))
    if not files:
        return None
    frame = pl.concat([pl.read_parquet(path) for path in files], how="diagonal_relaxed")
    columns = set(frame.columns)
    ordinal = choose_first_column(columns, ("origin_ordinal", "ordinal"))
    if ordinal is None:
        return frame
    return frame.unique(subset=[ordinal], keep="first", maintain_order=True)


def _align_labels(labels: pl.DataFrame, origins: pl.DataFrame, origin_ordinal_col: str) -> pl.DataFrame:
    label_ordinal_col = choose_first_column(set(labels.columns), ("origin_ordinal", "ordinal"))
    if label_ordinal_col is None:
        return labels.head(origins.height)
    if label_ordinal_col != origin_ordinal_col:
        labels = labels.rename({label_ordinal_col: origin_ordinal_col})
    base = origins.select([origin_ordinal_col])
    return base.join(labels, on=origin_ordinal_col, how="left")


def write_block_manifests(
    args: argparse.Namespace,
    job: PackageJob,
    events: pl.DataFrame,
    origins: pl.DataFrame,
    event_feature_names: tuple[str, ...],
    events_path: Path,
    origins_path: Path,
    label_rel: str | None,
) -> int:
    root = job.output_package_dir.parents[1]
    blocks_dir = job.output_package_dir / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    max_origins = max(1, int(args.max_origins_per_block))
    max_events = max(1, int(args.max_events_per_block))
    context_events = max(1, int(args.context_events))
    event_ordinal_col = choose_first_column(set(events.columns), ("ordinal", "event_ordinal"))
    origin_ordinal_col = choose_first_column(set(origins.columns), ("origin_ordinal", "ordinal"))
    origin_time_col = choose_first_column(set(origins.columns), ("origin_timestamp_us", "timestamp_us", "sip_timestamp_us"))
    event_ordinals = events[event_ordinal_col].to_numpy()
    origin_positions = origins["origin_event_index"].to_numpy()
    origin_ordinals = origins[origin_ordinal_col].to_numpy()
    origin_times = origins[origin_time_col].to_numpy()
    blocks = 0
    origin_start = 0
    while origin_start < origins.height:
        event_start = max(0, int(origin_positions[origin_start]) - (context_events - 1))
        origin_end = min(origins.height, origin_start + max_origins)
        while origin_end > origin_start and int(origin_positions[origin_end - 1]) - event_start + 1 > max_events:
            origin_end -= 1
        if origin_end <= origin_start:
            origin_end = origin_start + 1
        event_end = min(events.height, int(origin_positions[origin_end - 1]) + 1)
        block_id = f"block_{blocks:06d}"
        manifest = PackedBlockManifest(
            block_id=block_id,
            month=job.month,
            ticker=job.ticker,
            ticker_dir_name=symbol_to_ticker_dir(job.ticker),
            source_cache_root=str(Path(args.source_cache_root)),
            event_path=str(events_path.relative_to(root)).replace("\\", "/"),
            origin_path=str(origins_path.relative_to(root)).replace("\\", "/"),
            label_path=label_rel,
            event_feature_names=event_feature_names,
            event_rows=int(event_end - event_start),
            origin_rows=int(origin_end - origin_start),
            event_start_index=int(event_start),
            event_end_index=int(event_end),
            origin_start_index=int(origin_start),
            origin_end_index=int(origin_end),
            first_origin_timestamp_us=int(origin_times[origin_start]) if origin_end > origin_start else None,
            last_origin_timestamp_us=int(origin_times[origin_end - 1]) if origin_end > origin_start else None,
            first_origin_ordinal=int(origin_ordinals[origin_start]) if origin_end > origin_start else None,
            last_origin_ordinal=int(origin_ordinals[origin_end - 1]) if origin_end > origin_start else None,
            first_event_ordinal=int(event_ordinals[event_start]) if event_end > event_start else None,
            last_event_ordinal=int(event_ordinals[event_end - 1]) if event_end > event_start else None,
            created_at_utc=utc_now_iso(),
            metadata={"context_events": context_events, "max_origins_per_block": max_origins, "max_events_per_block": max_events},
        )
        block_dir = job.output_package_dir / block_id
        block_dir.mkdir(parents=True, exist_ok=True)
        write_json(block_dir / "block_manifest.json", manifest.to_dict())
        write_json(blocks_dir / f"{block_id}.json", manifest.to_dict())
        blocks += 1
        origin_start = origin_end
    return blocks


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def job_to_dict(job: PackageJob) -> dict[str, str]:
    return {
        "month": job.month,
        "ticker": job.ticker,
        "source_package_dir": str(job.source_package_dir),
        "output_package_dir": str(job.output_package_dir),
        "ticker_dir_name": job.ticker_dir_name,
    }


class PackedBuilderReporter:
    def __init__(self, stats: BuilderStats, *, output_root: Path, layout: str = "auto") -> None:
        self.stats = stats
        self.output_root = output_root
        self.layout = layout
        self._live: Any | None = None

    def __enter__(self) -> "PackedBuilderReporter":
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._live = Live(self._render(), console=Console(), refresh_per_second=2, screen=True, auto_refresh=False, transient=False)
                self._live.start()
            except Exception:
                if self.layout == "rich":
                    raise
                self._live = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self.refresh(force=True)
            self._live.stop()

    def refresh(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
        elif self.layout == "text":
            with self.stats.lock:
                print(
                    f"packages={self.stats.packages_done}/{self.stats.packages_total} "
                    f"blocks={self.stats.blocks_written} origins={self.stats.origin_rows} failed={self.stats.packages_failed}",
                    flush=True,
                )

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table

        with self.stats.lock:
            done = self.stats.packages_done
            failed = self.stats.packages_failed
            total = max(1, self.stats.packages_total)
            blocks = self.stats.blocks_written
            events = self.stats.event_rows
            origins = self.stats.origin_rows
            messages = list(self.stats.messages)
            elapsed = max(0.001, time.perf_counter() - self.stats.started)
        progress = Progress(TextColumn("[bold]Packages"), BarColumn(bar_width=None), TextColumn("{task.completed}/{task.total}"), TextColumn("{task.percentage:>5.1f}%"), expand=True)
        progress.add_task("packages", total=total, completed=min(done + failed, total))
        rate = (done + failed) / elapsed
        remaining = max(0, total - done - failed)
        eta = remaining / rate if rate > 0 else 0.0
        summary = Table.grid(expand=False, padding=(0, 3))
        summary.add_column(no_wrap=True)
        summary.add_column(no_wrap=True)
        summary.add_row(f"[bold]Done[/] {done:,}/{total:,}", f"[bold]Failed[/] {failed:,}")
        summary.add_row(f"[bold]Blocks[/] {blocks:,}", f"[bold]Origins[/] {origins:,}")
        summary.add_row(f"[bold]Events[/] {events:,}", f"[bold]Speed[/] {rate:.2f} pkg/s")
        summary.add_row(f"[bold]Elapsed[/] {_fmt_seconds(elapsed)}", f"[bold]ETA[/] {_fmt_seconds(eta)}")
        summary.add_row(f"[bold]Output[/] {self.output_root}", "")
        msg_table = Table.grid(expand=True)
        for msg in messages[-8:]:
            msg_table.add_row(msg)
        return Group(Panel(Group(progress, summary), title="Packed Market Cache Builder", border_style="cyan"), Panel(msg_table, title="Messages", border_style="yellow"))


def _fmt_seconds(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


if __name__ == "__main__":
    raise SystemExit(main())
