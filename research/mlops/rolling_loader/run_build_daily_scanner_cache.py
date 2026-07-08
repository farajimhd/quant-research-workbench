from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
    print(f"SCANNER BUILD month={args.month} days={len(grouped):,} files={len(jobs):,} cache={cache_root}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = {
            pool.submit(build_day_scanner, args=args, month_dir=month_dir, source_date=source_date, files=files): source_date
            for source_date, files in sorted(grouped.items())
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"DAY {result['source_date']} rows={result['rows']:,} files={result['files']:,} "
                f"bytes={result['bytes']:,} seconds={result['seconds']:.1f} path={result['path']}",
                flush=True,
            )
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
    print(f"SUMMARY days={len(results):,} rows={sum(int(r['rows']) for r in results):,} seconds={time.perf_counter() - started:.1f}", flush=True)
    return 0


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


def build_day_scanner(*, args: argparse.Namespace, month_dir: Path, source_date: str, files: list[Path]) -> dict[str, Any]:
    import polars as pl

    started = time.perf_counter()
    frames = [pl.read_parquet(path) for path in files]
    base = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
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
    output = month_dir / "global" / "scanner" / f"scanner_{source_date}.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not bool(args.overwrite):
        raise FileExistsError(f"Scanner artifact already exists: {output}. Pass --overwrite to replace it.")
    tmp = output.with_name(f"{output.name}.{time.time_ns()}.tmp")
    frame.write_parquet(tmp, compression="zstd")
    tmp.replace(output)
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
